---
title: Huddle
repo: ..
out: docs/book
commit: 7a9ef26a4ac60761609a9ec407b5907f406c423c
lang: en
max_pages: 6
max_page_words: 900
max_note_words: 180
max_words: 4000
pages:
  - overview: Overview
  - how-it-works: How a turn works
  - steering: Steering mid-answer
  - groups-and-tools: Groups, memory and tools
  - testing-and-evaluation: Testing and evaluation
  - limits: Limits and known gaps
ignore:
  - '**/*.lock'
  - '**/package-lock.json'
  - eval/results/**
  - docs/img/**
  - '**/*.png'
  - frontend/dist/**
  - frontend/src/styles.css
  - frontend/.prettierrc
  - frontend/.gitignore
  - frontend/.dockerignore
  - .gitignore
  - .dockerignore
langs:
  - zh
---

This book is for someone evaluating Huddle: what it does, how one message flows through the runtime, how mid-answer steering stays consistent, and where it stops. It builds on the [architecture overview](../architecture.md), [configuration](../configuration.md), [SECURITY.md](../../SECURITY.md) and the [specs](../specs/) instead of repeating them.
