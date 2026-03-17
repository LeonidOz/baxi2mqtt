import pytest

from reconnection_manager import ReconnectionManager, ReconnectionState


@pytest.mark.asyncio
async def test_attempt_reconnection_treats_false_result_as_failure():
    manager = ReconnectionManager(name="Test", max_retries=3, base_delay=0, max_delay=0)

    async def connect_func():
        return False

    success = await manager.attempt_reconnection(connect_func)

    assert success is False
    assert manager.state == ReconnectionState.FAILED
    assert manager.successful_connections == 0
    assert manager.total_errors == 1


@pytest.mark.asyncio
async def test_attempt_reconnection_success_path():
    manager = ReconnectionManager(name="Test", max_retries=3, base_delay=0, max_delay=0)

    async def connect_func():
        return True

    success = await manager.attempt_reconnection(connect_func)

    assert success is True
    assert manager.state == ReconnectionState.CONNECTED
    assert manager.successful_connections == 1
