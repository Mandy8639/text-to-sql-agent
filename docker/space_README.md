---
title: Text-to-SQL Analytics Agent
emoji: 📊
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Ask an e-commerce database questions in plain English
---

# Text-to-SQL Analytics Agent (demo)

Ask the Olist Brazilian e-commerce database a business question in plain English. The agent
retrieves the relevant schema and examples, writes PostgreSQL, checks it with a safety
validator, runs it read-only, and explains the result.

This Space runs the demo build: Claude Haiku 5.5, a per-visitor and global rate limit, and a
daily spend cap. Source, evaluation results and design decisions are in the GitHub repository.
