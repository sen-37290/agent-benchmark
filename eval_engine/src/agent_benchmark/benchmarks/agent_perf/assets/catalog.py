CATALOG = [
    {
        "id": "agent_shallow",
        "description": "Shallow multi-turn agent workload with light tool calls",
        "preset": "agent_shallow",
        "turns": 5,
        "category": "shallow_chat",
    },
    {
        "id": "agent_deep",
        "description": "Deep multi-turn agent workload testing long-context KV cache pressure",
        "preset": "agent_deep",
        "turns": 20,
        "category": "deep_context",
    },
    {
        "id": "agent_bursty",
        "description": "Bursty agent workload with frequent parallel tool execution calls",
        "preset": "agent_bursty",
        "turns": 10,
        "category": "bursty_tools",
    },
    {
        "id": "agent_tool_heavy",
        "description": "Tool-heavy multi-turn agent workload with short output generation",
        "preset": "agent_tool_heavy",
        "turns": 15,
        "category": "tool_heavy",
    },
]
