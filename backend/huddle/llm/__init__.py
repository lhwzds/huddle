from .base import ChatMessage, Done, LLM, TextDelta, ToolCall, ToolCallDone, ToolSpec, Usage, complete_json
from .fake import FakeLLM, FakeReply
from .openai_chat import OpenAIChat
from .openai_responses import OpenAIResponses

def make_llm(cfg, model_key):
    model_cfg = cfg["models"][model_key]
    if model_cfg["provider"] == "openai_chat":
        return OpenAIChat(model_cfg)
    if model_cfg["provider"] == "openai_responses":
        return OpenAIResponses(model_cfg)
    if model_cfg["provider"] == "fake":
        def demo(call):
            if call.profile == "decide":
                return FakeReply('{"speak": true, "point": "hi", "effort": "light", "addresses": []}', delay=0)
            if call.profile == "utility":
                return FakeReply('{"drop": [], "instructions": []}', delay=0)
            return FakeReply(f"{(call.agent or 'Agent').capitalize()} here.", delay=0)
        return FakeLLM([demo])
    raise ValueError(f"Unknown model provider: {model_cfg['provider']}")
