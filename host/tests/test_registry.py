import asyncio
import pytest
from mazhost.universal_registry import EventBus, Event, UniversalCapabilityRegistry, Capability

@pytest.mark.asyncio
async def test_event_bus_pub_sub():
    bus = EventBus()
    events_received = []

    async def handler(ev: Event):
        events_received.append(ev)

    bus.subscribe("test.event", handler)
    await bus.publish(Event(type="test.event", source="unit_test", payload={"foo": "bar"}))

    assert len(events_received) == 1
    assert events_received[0].payload["foo"] == "bar"

@pytest.mark.asyncio
async def test_capability_execution():
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
