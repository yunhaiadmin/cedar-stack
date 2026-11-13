"""群抽奖发奖并发回归：两个开奖者（管理员手动 + 自动调度）同时 distribute 同一轮，
各自读到 distributed=False 时不能双倍发奖。"""
import hashlib
import sys

sys.path.insert(0, ".")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import models
from backend import lottery


def test_concurrent_distribute_pays_once(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lot.db'}")
    for t in (models.WebUser, models.SystemConfig, models.LotteryRound, models.LotteryRoundPrize,
              models.LotteryRoundEntry, models.LotteryRoundWinner, models.LotteryBlacklist,
              models.PointsLog, models.WelfareGrantLog):
        t.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    a, b = Session(), Session()

    user = models.WebUser(username="w", password_hash="", telegram_id=1)
    a.add(user)
    a.commit()
    rnd = lottery.create_round(a, title="t", chat_id=1,
                               prizes=[{"name": "天数奖", "type": "days", "value": 7, "quantity": 1}])
    rnd.seed = "s"
    rnd.seed_hash = hashlib.sha256(b"s").hexdigest()
    a.commit()
    lottery.join_round(a, rnd, user, 1)
    lottery.draw_round(a, rnd.id)
    rid, uid = rnd.id, user.id

    # 开奖者 A 已读到中奖行（distributed=False）……
    stale = a.query(models.LotteryRoundWinner).filter_by(round_id=rid).all()
    assert stale and not stale[0].distributed
    # ……与此同时开奖者 B 完整发完并提交
    assert lottery.distribute_round(b, rid)["distributed"] == 1
    # A 继续用身份映射里的旧对象发奖：不能再发一次
    res = lottery.distribute_round(a, rid)
    assert res["distributed"] == 0

    b.expire_all()
    # 只发一次：公益资格续 7 天、留痕一条（积分已下线，改查 WelfareGrantLog）
    refreshed = b.query(models.WebUser).filter_by(id=uid).one()
    assert refreshed.is_welfare is True
    assert refreshed.welfare_expires_at is not None
    from datetime import datetime as _dt, timedelta as _td
    assert abs((refreshed.welfare_expires_at - _dt.now()).total_seconds() - 7 * 86400) < 120
    logs = b.query(models.WelfareGrantLog).filter_by(user_id=uid).all()
    assert len(logs) == 1
    assert logs[0].channel == "lottery_win" and logs[0].days == 7
    a.close()
    b.close()
