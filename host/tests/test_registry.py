import asyncio
from mazhost.universal_registry import EventBus, Event, UniversalCapabilityRegistry, Capability

def test_event_bus_pub_sub():
    asyncio.run(_pub_sub())


async def _pub_sub():
    bus = EventBus()
    events_received = []

    async def handler(ev: Event):
        events_received.append(ev)

    bus.subscribe("test.event", handler)
    await bus.publish(Event(type="test.event", source="unit_test", payload={"foo": "bar"}))

    assert len(events_received) == 1
    assert events_received[0].payload["foo"] == "bar"

def test_capability_execution():
    asyncio.run(_capability_execution())


async def _capability_execution():
    bus = EventBus()
    registry = UniversalCapabilityRegistry(bus)

    async def sample_handler(args, context):
        return {"echo": args.get("input", "") * 2}

    registry.register(Capability(
        id="test.echo",
        name="Echo Test",
        description="Echoes input",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        category="testing",
        handler=sample_handler
    ))

    res = await registry.execute("test.echo", {"input": "hello"})
    assert res["success"] is True
    assert res["result"]["echo"] == "hellohello"


def test_wildcard_subscriber_called_once_per_publish():
    async def run():
        bus = EventBus()
        seen = []
        bus.subscribe("a", lambda ev: seen.append("a"))
        bus.subscribe("*", lambda ev: seen.append("*"))
        for _ in range(3):
            await bus.publish(Event(type="a", source="t", payload={}))
        assert seen == ["a", "*"] * 3
        assert len({e.id for e in bus.get_history()}) == 3
    asyncio.run(run())
