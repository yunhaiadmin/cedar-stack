# -*- coding: utf-8 -*-
"""转存工人唤醒订阅断裂时的认领兜底（2026-11-02 生产实证）。

生产故障：worker 04:56 重启后，唤醒订阅因 Redis 客户端 socket_timeout
（1 秒）每次 listen 都抛 TimeoutError，订阅线程每约 31 秒断开重连，
期间新单的即时认领通道事实上失效；主循环只剩 60 秒兜底扫。实测
认领节奏：启动突发后停 4–7 分钟一轮，跨槽 3 槽空着、13 单 pending
无人认领，user 1329 的 playback 单 2123 等了 26 分钟以上。

本文件钉死两条：
1. 订阅线程一次订阅失败（抛错）也必须唤醒主循环跑认领——断连
   本身就是一次认领触发信号，不能只等 30 秒后重连再说。
2. 端到端：订阅已断裂时，主循环仍应在数秒内认领到轮内新建的
   playback 单，而不是等满 60 秒兜底扫。

2026-11-23 续：旧实现用 listen() 阻塞读，空闲 1 秒即抛
TimeoutError，被当断连走异常路径——订阅约 31 秒周期里 30 秒
是死的（期间发布的消息直接丢失），且每周期刷一条 warning
堆栈（生产实测 2 小时 231 条）。改用 get_message(timeout=…)
轮询后本文件再钉死三条：空闲超时静默（无 warning、不误唤醒、
循环持续轮询）；真断连（ConnectionError）的 warning＋唤醒
行为逐字不变；真实唤醒消息能到达并置唤醒 Event。

全部离线：SQLite 临时文件库 + 桩 drive_api + 桩 Redis 客户端；
订阅重连等待用封顶 Event 加速（真实间隔 30 秒不变）。
"""
import os

os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("REDIS_ENABLED", "false")

import logging
import threading
import time
from types import SimpleNamespace

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import database as dbmod
from backend import models as base_models
from backend.emby_server import models as em
from backend.emby_server import transfer_variant as tv


@pytest.fixture()
def db_factory(tmp_path):
    # 临时文件库，不用 :memory:+StaticPool：StaticPool 让主线程与守护/
    # 订阅线程共用同一条 DBAPI 连接，任一会话的回滚会顺带撤销其他会话
    # 刚提交的写入——job 插入被撤销后 job.id 读回抛 ObjectDeletedError、
    # 认领更新被撤销后状态久停中间态，全量负载下间歇复现（2026-11-02
    # CI 多个 PR 同形态冒烟红；本地复现：StaticPool 口径 2/30 出同签名
    # ObjectDeletedError、文件库口径 0/30）。文件库每会话独立连接，
    # 与生产（PG）连接语义一致。
    engine = create_engine(
        f"sqlite:///{tmp_path}/claim_wake.db",
        connect_args={"check_same_thread": False, "timeout": 30})
    base_models.Base.metadata.create_all(engine)
    em.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(db_factory):
    session = db_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _reset_pause_and_wake(monkeypatch):
    tv._paused_until.clear()
    tv._WAKE_EVENT.clear()
    yield
    tv._paused_until.clear()
    tv._WAKE_EVENT.clear()


class _CappedEvent(threading.Event):
    """wait 超时封顶的 Event：订阅重连的 30 秒等待在测试里按
    0.5 秒跑，真实间隔不变；is_set/set 语义与标准 Event 一致。"""

    def wait(self, timeout=None):  # noqa: D102 - 测试替身
        if timeout is None:
            return super().wait()
        return super().wait(min(timeout, 0.5))


class _BrokenPubSub:
    """连接已断的订阅替身：listen / get_message 都抛
    ConnectionError，复现 Redis 真断连（重启/网络断）时的行为。"""

    def subscribe(self, *args, **kwargs):
        return None

    def listen(self):
        raise RedisConnectionError("connection lost")

    def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        raise RedisConnectionError("connection lost")


class _BrokenRedisClient:
    def pubsub(self):
        return _BrokenPubSub()


class _IdlePubSub:
    """空闲订阅替身：忠实复刻生产 redis-py 在 socket_timeout=1 秒
    客户端下的两副面孔——listen() 阻塞读空闲即抛 TimeoutError；
    get_message(timeout=…) 用 select 等待，空闲超时在其内部
    消化、返回 None 不抛错。通道里永远没有消息。"""

    def __init__(self):
        self.get_message_calls = 0

    def subscribe(self, *args, **kwargs):
        return None

    def listen(self):
        raise TimeoutError("timed out")

    def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        self.get_message_calls += 1
        time.sleep(0.02)
        return None


class _IdleRedisClient:
    def __init__(self):
        self.pubsub_instance = _IdlePubSub()

    def pubsub(self):
        return self.pubsub_instance


class _MessagePubSub:
    """有唤醒消息到达的订阅替身：先回一条订阅确认，再回真消息，
    之后回到空闲。"""

    def __init__(self):
        self._queue = [
            {"type": "subscribe",
             "channel": tv.TRANSFER_WORKER_WAKE_CHANNEL, "data": 1},
            {"type": "message",
             "channel": tv.TRANSFER_WORKER_WAKE_CHANNEL, "data": "42"},
        ]

    def subscribe(self, *args, **kwargs):
        return None

    def get_message(self, ignore_subscribe_messages=False, timeout=0.0):
        if self._queue:
            return self._queue.pop(0)
        time.sleep(0.02)
        return None


class _MessageRedisClient:
    def pubsub(self):
        return _MessagePubSub()


def _job(db, item_id=1, trigger="playback", owner_ref="u9"):
    row = em.TransferJob(item_id=item_id, trigger=trigger,
                         owner_scope="user", owner_ref=owner_ref,
                         status="pending")
    db.add(row)
    db.commit()
    return row


def test_wake_subscriber_failure_wakes_main_loop(monkeypatch):
    """订阅失败也要唤醒：订阅抛错（真断连）后，进程内唤醒 Event
    必须置位，主循环下一轮即跑认领，而不是等 30 秒重连后再说。"""
    monkeypatch.setattr(dbmod, "get_redis",
                        lambda: _BrokenRedisClient())
    stop = _CappedEvent()
    thread = threading.Thread(target=tv._wake_subscriber_loop, args=(stop,),
                              daemon=True)
    thread.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not tv._WAKE_EVENT.is_set():
            time.sleep(0.05)
        assert tv._WAKE_EVENT.is_set() is True
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_daemon_claims_late_job_while_subscription_broken(
        db_factory, monkeypatch):
    """端到端：订阅已断裂时，主循环应在数秒内认领轮内新建的单。

    复现 2026-11-02 现场：工人已在岗、订阅断裂、兜底扫 60 秒；
    轮内新建的 playback 单（job 2123 同款处境）必须在 10 秒内被
    认领，而不是等满 60 秒扫到。执行本身打桩为 done，只测认领。
    """
    monkeypatch.setattr(dbmod, "get_redis",
                        lambda: _BrokenRedisClient())
    monkeypatch.setattr(tv, "master_enabled", lambda: True)

    def _fake_run(db, job, factory):
        job.status = "done"
        db.commit()
        return {"outcome": "done"}

    monkeypatch.setattr(tv, "run_transfer_job_once", _fake_run)
    stop = _CappedEvent()
    state = {"last_patrol": 0.0, "last_follow_sweep": 0.0,
             "last_idle_cleanup": 0.0}
    subscriber = threading.Thread(target=tv._wake_subscriber_loop,
                                  args=(stop,), daemon=True)
    main = threading.Thread(
        target=tv._daemon_main_loop,
        args=(stop, state, db_factory, lambda: SimpleNamespace()),
        daemon=True)
    subscriber.start()
    main.start()
    try:
        # 等主循环跑完首轮空转、进入等待态，再建单（轮内到达）。
        time.sleep(1.0)
        session = db_factory()
        job = _job(session)
        job_id = job.id
        session.close()
        deadline = time.time() + 10
        status = None
        while time.time() < deadline:
            check = db_factory()
            try:
                row = (check.query(em.TransferJob)
                       .filter(em.TransferJob.id == job_id).one())
                status = row.status
            finally:
                check.close()
            if status == "done":
                break
            time.sleep(0.1)
        assert status == "done"
    finally:
        stop.set()
        subscriber.join(timeout=5)
        main.join(timeout=5)
    assert not subscriber.is_alive()
    assert not main.is_alive()


def test_wake_subscriber_idle_timeout_stays_silent(monkeypatch, caplog):
    """空闲超时必须静默：轮询期间通道无消息时，不许打「唤醒订阅
    断开」warning、不许误置唤醒 Event、循环必须持续轮询。

    回归对象：旧 listen() 实现把每 1 秒一次的空闲 TimeoutError
    当断连处理，约 31 秒刷一条 warning 堆栈（生产 2 小时 231
    条），且每次都误唤醒主循环。本用例的替身对旧实现走 listen
    路径，warning、误唤醒与零轮询都会让断言失败（先红后绿）。"""
    client = _IdleRedisClient()
    monkeypatch.setattr(dbmod, "get_redis", lambda: client)
    stop = _CappedEvent()
    thread = threading.Thread(target=tv._wake_subscriber_loop, args=(stop,),
                              daemon=True)
    with caplog.at_level(logging.WARNING, logger=tv.__name__):
        thread.start()
        try:
            time.sleep(1.0)
            assert client.pubsub_instance.get_message_calls >= 5
            assert tv._WAKE_EVENT.is_set() is False
            assert thread.is_alive()
        finally:
            stop.set()
            thread.join(timeout=5)
    assert not thread.is_alive()
    assert "唤醒订阅断开" not in caplog.text


def test_wake_subscriber_connection_error_warns_and_wakes(
        monkeypatch, caplog):
    """真断连行为不变：get_message 抛 ConnectionError 时，必须打
    「唤醒订阅断开」warning 并置唤醒 Event（断连即认领触发信号，
    2026-11-02 补偿语义逐字保留）。"""
    monkeypatch.setattr(dbmod, "get_redis",
                        lambda: _BrokenRedisClient())
    stop = _CappedEvent()
    thread = threading.Thread(target=tv._wake_subscriber_loop, args=(stop,),
                              daemon=True)
    with caplog.at_level(logging.WARNING, logger=tv.__name__):
        thread.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not tv._WAKE_EVENT.is_set():
                time.sleep(0.05)
            assert tv._WAKE_EVENT.is_set() is True
        finally:
            stop.set()
            thread.join(timeout=5)
    assert not thread.is_alive()
    assert "唤醒订阅断开" in caplog.text


def test_wake_subscriber_message_wakes_main_loop(monkeypatch, caplog):
    """收到真实唤醒消息必须置唤醒 Event，且不打断连 warning。

    旧 listen() 实现下订阅约 31 秒里 30 秒处于断开重连状态，
    期间发布的消息直接丢失；轮询实现里订阅连接持续有效，消息
    能真正到达——本用例钉死消息路径本身（含订阅确认不误触发）。"""
    monkeypatch.setattr(dbmod, "get_redis",
                        lambda: _MessageRedisClient())
    stop = _CappedEvent()
    thread = threading.Thread(target=tv._wake_subscriber_loop, args=(stop,),
                              daemon=True)
    with caplog.at_level(logging.WARNING, logger=tv.__name__):
        thread.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not tv._WAKE_EVENT.is_set():
                time.sleep(0.05)
            assert tv._WAKE_EVENT.is_set() is True
        finally:
            stop.set()
            thread.join(timeout=5)
    assert not thread.is_alive()
    assert "唤醒订阅断开" not in caplog.text
