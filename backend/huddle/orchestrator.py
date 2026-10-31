"""Conversation control: parallel decisions, deduplication, rounds and interrupts."""
from __future__ import annotations

import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field

from . import prompts
from .agent import AgentRun, RunContext, idle_state
from .agents import AgentRegistry
from .language import load_language, name_pattern
from .context import build_context, load_state
from .llm import ChatMessage, complete_json
from .metering import MeteredLLM

log = logging.getLogger(__name__)
@dataclass
class Conversation:
    lock: object
    turn: int | None = None
    runs: dict[str, AgentRun] = field(default_factory=dict)
    turn_task: asyncio.Task | None = None
    instruction_task: asyncio.Task | None = None
    instruction_tasks: list = field(default_factory=list)
    state: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(self, cfg, store, llm_factory, sandbox, meter=None, registry=None, language=None):
        self.cfg, self.store, self.llm_factory, self.sandbox = cfg, store, llm_factory, sandbox
        # The meter counts the tokens of every model call for the daily budget.
        self.meter = meter
        # Agents can be added and edited while the app runs; always read them from here.
        self.registry = registry if registry is not None else AgentRegistry(store, cfg)
        self.conversations: dict[str, Conversation] = {}
        self._llms = {}
        self._tasks = set()
        self._closed = False
        self._names_cache = {}   # group -> (registry version, {agent key: pattern})
        self._conv_groups = {}   # conversation id -> group key
        # Intent word lists (stop, everyone, file, discussion, hand-off) live in config/language.yaml.
        self.language = language if language is not None else load_language(cfg)

    @property
    def agents(self):
        """The default group's members (for callers without a conversation)."""
        return self.registry.all()

    def group_of(self, conv_id) -> str:
        return self._conv_groups.get(conv_id) or self.registry.default

    async def ensure_group(self, conv_id) -> str:
        """Learn which group a conversation belongs to (stored with the conversation)."""
        if conv_id not in self._conv_groups:
            conv = await self.store.get_conversation(conv_id)
            group = (conv or {}).get("group") or self.registry.default
            self._conv_groups[conv_id] = group if group in {g["key"] for g in self.registry.groups()} \
                else self.registry.default
        return self._conv_groups[conv_id]

    def members(self, conv_id=None):
        return self.registry.all(self.group_of(conv_id))

    def _patterns(self, conv_id=None):
        group = self.group_of(conv_id)
        version, patterns = self._names_cache.get(group, (None, None))
        if version != self.registry.version:
            patterns = {a.key: name_pattern([a.name, a.key]) for a in self.members(conv_id)}
            self._names_cache[group] = (self.registry.version, patterns)
        return patterns

    def _conversation(self, conv_id):
        if conv_id not in self.conversations:
            self.conversations[conv_id] = Conversation(self.store.lock(conv_id), state=self._empty_state(conv_id))
        conv = self.conversations[conv_id]
        # Agents added since the conversation was loaded get an idle entry.
        for key, entry in self._empty_state(conv_id).items():
            conv.state.setdefault(key, entry)
        return conv

    def _empty_state(self, conv_id=None):
        return {a.key: idle_state(self.cfg["limits"]["context_tokens"])
                for a in self.members(conv_id)}

    def agents_state(self, conv_id):
        state = self._empty_state(conv_id)
        conv = self.conversations.get(conv_id)
        if conv:
            state.update({k: v for k, v in conv.state.items() if k in state})
        return copy.deepcopy(state)

    def is_active(self, conv_id) -> bool:
        """True while the conversation still has work in flight.

        Active means the turn driver is running, an agent run is alive, or an
        instruction extraction is running. The snapshot reads this under the
        conversation lock, so it agrees with the rest of the snapshot.
        """
        conv = self.conversations.get(conv_id)
        if conv is None:
            return False
        if conv.turn_task is not None and not conv.turn_task.done():
            return True
        # Every extraction of this conversation counts, not only the newest:
        # an older one may still be running while a newer one waits for it.
        for task in conv.instruction_tasks:
            if task is not None and not task.done():
                return True
        if conv.instruction_task is not None and not conv.instruction_task.done():
            return True
        for run in conv.runs.values():
            if not run.finished:
                return True
            # A stopped run is still alive while it cleans up.
            if run.task is not None and not run.task.done():
                return True
        return False

    def _metered(self, agent=None):
        """The model of an agent, wrapped so its tokens are counted."""
        llm = self._llm(agent)
        if self.meter is None:
            return llm
        return MeteredLLM(llm, self.meter)

    def active_conversations(self) -> list[str]:
        """The ids of the conversations that are working right now."""
        return [conv_id for conv_id in self.conversations if self.is_active(conv_id)]

    def _llm(self, agent=None):
        key = agent.model if agent and agent.model else self.cfg["model"]
        if key not in self._llms:
            self._llms[key] = self.llm_factory(self.cfg, key)
        return self._llms[key]

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        def done(t):
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("Runtime task failed", exc_info=t.exception())
        task.add_done_callback(done)
        return task

    def targets(self, text, conv_id=None):
        """Agent keys the text addresses.

        A name addresses that agent. A word for the whole group ("everyone",
        "all of you", "你们") addresses every agent, so the whole
        roster is returned and everyone counts as addressed.
        """
        found = {key for key, pattern in self._patterns(conv_id).items() if pattern.search(text)}
        if self.language.addresses_everyone(text):
            found.update(a.key for a in self.members(conv_id))
        return found

    def _hard_stop(self, text, conv_id=None):
        return self.language.is_stop(text, list(self._patterns(conv_id).values()))

    @staticmethod
    def _cancel_turn(conv):
        if conv.turn_task and not conv.turn_task.done():
            conv.turn_task.cancel()

    async def on_user_message(self, conv_id, text):
        if self._closed:
            raise RuntimeError("Orchestrator is closed")
        await self.ensure_group(conv_id)
        conv = self._conversation(conv_id)
        stopped = []
        async with conv.lock:
            if conv.turn is None:
                conv.turn = await self.store.last_turn(conv_id)
            conv.turn += 1
            message = await self.store.add_message(conv_id, author="user", turn=conv.turn, text=text, status="done")
            targets = self.targets(text, conv_id)
            if not targets and not conv.runs and not self._hard_stop(text, conv_id):
                # "ok, just the short version" right after stopping someone is meant for them.
                targets = self._stopped_followup_targets(await self.store.messages(conv_id), message["id"],
                                                         conv_id)
            if self._hard_stop(text, conv_id):
                self._cancel_turn(conv)
                stopped = await self._stop_locked(conv_id, conv, targets)
                stopped += [t for t in conv.instruction_tasks if t is not None]
            else:
                eligible = {a.key for a in self.members(conv_id) if a.key not in conv.runs}
                steered = set()
                for key, run in list(conv.runs.items()):
                    if not targets or key in targets:
                        if self.language.wants_remember(text):
                            run.ctx.can_remember = True
                        if not run.steer(message["id"]):
                            eligible.add(key)
                        else:
                            steered.add(key)
                    elif run.finished:
                        eligible.add(key)
                # Extractors are ordered by arrival so persistent directives keep
                # their original precedence even when users send a burst.
                previous = conv.instruction_task
                # Keep only the extractions that are still running.
                conv.instruction_tasks = [t for t in conv.instruction_tasks if not t.done()]
                conv.instruction_task = self._track(self._extract(conv_id, message, previous))
                conv.instruction_tasks.append(conv.instruction_task)
                self._cancel_turn(conv)
                candidates = [a for a in self.members(conv_id) if a.key in eligible]
                # Idle agents should not be forced into a turn that only steered writers.
                pure_steer = bool(steered) and (targets <= steered if targets else not (set(conv.runs) - steered))
                conv.turn_task = self._track(self._turn(conv_id, conv, conv.turn, message, targets,
                                                       candidates, previous, pure_steer))
        if stopped:
            await asyncio.gather(*stopped, return_exceptions=True)
        return message

    async def _extract(self, conv_id, message, previous):
        try:
            if previous is not None:
                try:
                    await asyncio.shield(previous)
                except asyncio.CancelledError:
                    # A stopped predecessor must not cancel this extraction.
                    if not previous.cancelled() or asyncio.current_task().cancelling():
                        raise
            instructions = await self.store.instructions(conv_id)
            roster = self.members(conv_id)
            messages = [ChatMessage("system", prompts.utility_system(roster, instructions)),
                        ChatMessage("user", prompts.extract_instructions(message["text"], roster))]
            async with asyncio.timeout(self.cfg["limits"]["extract_timeout_s"]):
                result = await complete_json(self._metered(), messages, profile="utility",
                                             max_tokens=self.cfg["limits"]["max_tokens"]["utility"])
            items = result.get("instructions", [])
            if not isinstance(items, list):
                return
            for item in items:
                if (isinstance(item, dict) and item.get("agent") in [None, *(a.key for a in roster)]
                        and isinstance(item.get("text"), str) and item["text"].strip()):
                    await self.store.add_instruction(conv_id, item.get("agent"), item["text"].strip(), message["id"])
        except Exception:
            log.warning("Instruction extraction failed", exc_info=True)

    def _run_context(self, conv_id, agent, *, can_remember=False):
        return RunContext(conv_id, self.cfg, self.store, self._metered(agent), self.sandbox,
                          self.members(conv_id), self._conversation(conv_id).state, self.registry,
                          self.group_of(conv_id), can_remember)

    async def _decide(self, conv_id, agent, round, addressed, discussion=False, combine=False):
        fallback = {"speak": addressed, "point": "", "effort": "normal", "addresses": []}
        try:
            async with asyncio.timeout(self.cfg["limits"]["decide_timeout_s"]):
                state = await load_state(self.store, conv_id, agent, self.members(conv_id), self.cfg["limits"])
                built = build_context(agent, state, purpose="decide")
                decision_prompt = prompts.decide(agent, round=round, addressed=addressed, discussion=discussion,
                                                 combine=combine)
                reminder = prompts.instruction_reminder(state.instructions, agent.key)
                if reminder:
                    decision_prompt += "\n\n" + reminder
                messages = built.messages + [ChatMessage("user", decision_prompt)]
                result = await complete_json(self._metered(agent), messages, profile="decide",
                                             max_tokens=self.cfg["limits"]["max_tokens"]["decide"])
                if not isinstance(result.get("speak"), bool):
                    raise ValueError("Invalid decision")
                effort = result.get("effort")
                if not isinstance(effort, str) or effort not in {"light", "normal", "deep"}:
                    effort = "normal"
                addresses = result.get("addresses")
                if not isinstance(addresses, list):
                    addresses = []
                point = result.get("point")
                return {"speak": result["speak"], "effort": effort,
                        "point": point if isinstance(point, str) else "",
                        "addresses": [key for key in addresses
                                      if isinstance(key, str) and key in {a.key for a in self.members(conv_id)} and key != agent.key]}
        except Exception:
            log.warning("Decision failed for %s", agent.key, exc_info=True)
            return fallback

    async def _dedupe(self, conv_id, decisions, targets):
        speakers = {key: d for key, d in decisions.items() if d["speak"]}
        if len(speakers) < 2:
            return speakers
        try:
            transcript = [{k: m[k] for k in ("author", "text")} for m in (await self.store.messages(conv_id))[-self.cfg["limits"]["dedupe_history_messages"]:]]
            instructions = await self.store.instructions(conv_id)
            messages = [ChatMessage("system", prompts.utility_system(self.members(conv_id), instructions)),
                        ChatMessage("user", prompts.dedupe(speakers, transcript, targets))]
            async with asyncio.timeout(self.cfg["limits"]["decide_timeout_s"]):
                result = await complete_json(self._metered(), messages, profile="utility",
                                             max_tokens=self.cfg["limits"]["max_tokens"]["utility"])
            drops = result.get("drop", [])
            if not isinstance(drops, list):
                return speakers
            kept = {key: d for key, d in speakers.items() if key in targets or key not in drops}
            return kept or {next(iter(speakers)): next(iter(speakers.values()))}
        except Exception:
            log.warning("Deduplication failed", exc_info=True)
            return speakers

    async def _execute(self, conv, run):
        try:
            return await run.run()
        finally:
            async with conv.lock:
                if conv.runs.get(run.agent.key) is run:
                    del conv.runs[run.agent.key]

    async def _resolve_held(self, conv_id, decisions, targets, runs, remaining):
        try:
            speakers = await self._dedupe(conv_id, decisions, targets)
            kept = set(key for key in runs if key in speakers)
            kept = set(list(key for key in runs if key in kept)[:remaining])
            for key, run in runs.items():
                if key in kept:
                    run.release()
                else:
                    run.cancel()
            return kept
        except BaseException:
            for run in runs.values():
                run.cancel()
            raise

    def _turn_cap(self, cap, size):
        """Groups bigger than big_group_size get room for everyone once, plus a few replies."""
        limits = self.cfg["limits"]
        return size + limits["big_group_extra"] if size > max(cap, limits["big_group_size"]) else cap

    def _stopped_followup_targets(self, history, current_id, conv_id=None):
        """Authors of the answers stopped just before this message (stop words in between are skipped)."""
        stopped = set()
        for m in reversed([m for m in history if m["id"] != current_id]):
            if m["author"] == "user":
                if self._hard_stop(m["text"], conv_id):
                    continue
                break
            if m["status"] == "stopped":
                stopped.add(m["author"])
        return {key for key in stopped if self.registry.get(self.group_of(conv_id), key)}

    def _message_targets(self, message, conv_id=None):
        """Members a finished agent message invites to answer next.

        Only the text counts: @Name, a question to them in the same sentence, or a hand-off
        (patterns in config/language.yaml). The decide step's `addresses` (stored as reply_to) is not
        used: models list everyone there, which turned every message into an invitation.
        """
        text = message["text"]
        recipients = {key for key, pattern in self._patterns(conv_id).items()
                      if self.language.invites(pattern, text)}
        recipients.discard(message["author"])
        return recipients

    async def _turn(self, conv_id, conv, turn, message, targets, candidates, instruction_task, pure_steer=False):
        # A run that refused a steer may still be closing its response or killing
        # a command. Let that cleanup finish before reusing the agent's slot.
        retiring = [conv.runs[a.key].task for a in candidates if a.key in conv.runs and conv.runs[a.key].task]
        if retiring:
            await asyncio.gather(*(asyncio.shield(task) for task in retiring), return_exceptions=True)
        if instruction_task:
            try:
                async with asyncio.timeout(self.cfg["limits"]["extract_timeout_s"]):
                    await asyncio.shield(instruction_task)
            except TimeoutError:
                pass
        limits = self.cfg["limits"]
        count = 0
        discussion = False
        # Bigger groups need room for everyone who has something to say once, plus a couple of replies.
        max_rounds = limits["max_agent_rounds"]
        size = len(self.members(conv_id))
        cap = limits["max_agent_messages_per_turn"]
        cap = self._turn_cap(cap, size)
        first_speaker = None
        wants_file = False
        combine = False  # after several agents shared their facts, one round to put them together
        spoken = []  # (agent key) of every message this turn, in order
        round = 0
        while candidates and round <= max_rounds and count < cap:
            values = await asyncio.gather(*(self._decide(conv_id, a, round, a.key in targets, discussion,
                                                         combine and round == 1)
                                            for a in candidates))
            decisions = {a.key: value for a, value in zip(candidates, values)}
            if round == 0 and not pure_steer and not any(d["speak"] for d in values):
                addressed = [a for a in candidates if a.key in targets]
                pointed = [a for a in candidates if decisions[a.key]["point"].strip()]
                pool = addressed or pointed or candidates
                history = await self.store.messages(conv_id)
                last = {a.key: -1 for a in pool}
                for m in history:
                    if m["author"] in last:
                        last[m["author"]] = m["pos"]
                # A follow-up with no name ("ok, the short version") continues with whoever
                # spoke last; only a conversation nobody has answered yet goes to the agent
                # who spoke least recently (roster order breaks ties).
                previous = next((m["author"] for m in reversed(history)
                                 if m["author"] != "user" and m["author"] in last), None)
                if not addressed and not pointed and previous:
                    chosen = next(a for a in pool if a.key == previous)
                else:
                    chosen = min(pool, key=lambda a: last[a.key])
                decisions[chosen.key] = {**decisions[chosen.key], "speak": True, "effort": "light"}
            if round == 0:
                text = message["text"]
                speaking = [a.key for a in candidates if decisions[a.key]["speak"]]
                wants_file = self.language.wants_file(text)
                discussion = self.language.wants_discussion(text) or (
                    not wants_file and len(speaking) >= 2
                    and any(decisions[k]["effort"] == "deep" for k in speaking))
                if discussion:
                    max_rounds = limits["max_discussion_rounds"]
                    cap = self._turn_cap(limits["max_discussion_messages"], size)
                if wants_file and speaking:
                    # Files need tools, and tools come with deep effort.
                    writers = [k for k in speaking if k in targets] or speaking[:1]
                    for k in writers:
                        decisions[k] = {**decisions[k], "effort": "deep"}
                if not targets and not discussion and len(speaking) >= 2 and all(
                        decisions[k]["effort"] == "light" for k in speaking):
                    # A light, general question gets one light answer, not a chorus.
                    keep = next((k for k in speaking if decisions[k]["point"].strip()), speaking[0])
                    for k in speaking:
                        if k != keep:
                            decisions[k] = {**decisions[k], "speak": False}
            if discussion and not wants_file:
                # Discussion messages are short turns, not work products: no tools, no duplicate files.
                for k, d in decisions.items():
                    if d["speak"] and d["effort"] == "deep":
                        decisions[k] = {**d, "effort": "normal"}
            proposed = {key: d for key, d in decisions.items() if d["speak"]}
            held = len(proposed) >= 2
            directive = prompts.DISCUSSION_TURN if discussion else None
            # Memory is shared by every conversation of the group: only an explicit request may change it.
            can_remember = self.language.wants_remember(message["text"])
            runs = {}
            async with conv.lock:
                if conv.turn != turn or self._closed:
                    return
                for agent in candidates:
                    if agent.key not in proposed or (agent.key in conv.runs and not conv.runs[agent.key].finished):
                        continue
                    d = proposed[agent.key]
                    run = AgentRun(self._run_context(conv_id, agent, can_remember=can_remember), agent, turn,
                                   d["effort"], d["addresses"], held=held, directive=directive)
                    conv.runs[agent.key] = run
                    run.task = self._track(self._execute(conv, run))
                    runs[agent.key] = run
            if held:
                # This resolution outlives the turn driver: a new user message
                # can steer held writers while cancelling the old driver.
                resolver = self._track(self._resolve_held(conv_id, decisions, targets, runs, cap - count))
                speakers = await asyncio.shield(resolver)
            else:
                speakers = proposed
            tasks = [run.task for key, run in runs.items() if key in speakers]
            count += len(tasks)
            if not tasks:
                break
            # A new user turn cancels this driver, never the writers it owns.
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)
            finished_ids = {runs[key].message_id for key in speakers if key in runs and runs[key].message_id}
            addressed = set()
            for finished in await self.store.messages(conv_id):
                if finished["id"] in finished_ids and finished["status"] == "done":
                    addressed.update(self._message_targets(finished, conv_id))
                    spoken.append(finished["author"])
                    first_speaker = first_speaker or finished["author"]
            round += 1
            async with conv.lock:
                idle = [a for a in self.members(conv_id) if a.key not in conv.runs or conv.runs[a.key].finished]
            # In a discussion everyone may react (a few times each). After a first round in which two or more
            # agents shared substantive facts, everyone gets one chance to combine them. Otherwise only agents
            # someone addressed may answer.
            per_agent = limits["max_discussion_messages_per_agent"]
            combine = (round == 1 and not discussion and len(set(spoken)) >= 2
                       and any(proposed[k]["effort"] != "light" for k in speakers if k in proposed))
            if discussion:
                candidates = [a for a in idle if spoken.count(a.key) < per_agent]
            elif combine:
                candidates = idle
            else:
                candidates = [a for a in idle if a.key in addressed]

        if (discussion and len(spoken) >= limits["conclusion_min_messages"] and len(set(spoken)) >= 2
                and first_speaker):
            await self._conclude(conv_id, conv, turn, first_speaker)

    async def _conclude(self, conv_id, conv, turn, key):
        """One agent closes a discussion with the group's conclusion (a normal message)."""
        agent = self.registry.get(self.group_of(conv_id), key)
        async with conv.lock:
            if agent is None or conv.turn != turn or self._closed or (
                    key in conv.runs and not conv.runs[key].finished):
                return
            run = AgentRun(self._run_context(conv_id, agent), agent, turn, "normal", [],
                           directive=prompts.conclude())
            conv.runs[key] = run
            run.task = self._track(self._execute(conv, run))
        await asyncio.gather(asyncio.shield(run.task), return_exceptions=True)

    async def _stop_locked(self, conv_id, conv, targets=()):
        """Finalize under the lock; await cancelled tasks only after releasing it.

        A concurrent message after this critical section is a fresh turn. Its
        output must not be swept up by an older stop's asynchronous cleanup.
        """
        tasks = []
        # A stop ends the conversation's work: no extraction may run or write
        # after it, so every extraction of this conversation is cancelled.
        for task in conv.instruction_tasks:
            if task is not None and not task.done():
                task.cancel()
        if conv.instruction_task is not None and not conv.instruction_task.done():
            conv.instruction_task.cancel()
        for key, run in list(conv.runs.items()):
            if targets and key not in targets:
                continue
            run.cancel()
            if run.task:
                tasks.append(run.task)
        for m in await self.store.messages(conv_id):
            if m["status"] == "streaming" and (not targets or m["author"] in targets):
                await self.store.finish_message(m["id"], "stopped")
        for key, state in conv.state.items():
            if targets and key not in targets:
                continue
            run = conv.runs.get(key)
            if run:
                run.mark_stopped()
            if state["typing"]:
                state["typing"] = False
                await self.store.emit(conv_id, "agent.typing", {"agent": key, "typing": False})
        return tasks

    async def on_stop(self, conv_id):
        await self.ensure_group(conv_id)
        conv = self._conversation(conv_id)
        async with conv.lock:
            turn_task = conv.turn_task
            extractions = [t for t in conv.instruction_tasks if t is not None]
            if conv.instruction_task is not None and conv.instruction_task not in extractions:
                extractions.append(conv.instruction_task)
            self._cancel_turn(conv)
            tasks = await self._stop_locked(conv_id, conv)
        for task in [turn_task, *extractions]:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self._closed = True
        for conv_id in list(self.conversations):
            await self.on_stop(conv_id)
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
