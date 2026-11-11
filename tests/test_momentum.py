"""量能观察层纯函数：环比、放量倍数、阈值、汇总文案不越界。"""
from ntm import momentum as m


def test_amount_change_and_pct():
    assert m.amount_change(150.0, 60.0) == 90.0
    assert m.amount_change_pct(150.0, 60.0) == 150.0
    assert m.amount_change_pct(30.0, 60.0) == -50.0


def test_zero_previous_is_safe():
    assert m.amount_change_pct(50.0, 0) == 0.0
    assert m.volume_multiple(50.0, 0) == 0.0
    assert m.moving_average([]) == 0.0


def test_moving_average():
    assert m.moving_average([10, 20, 30, 40, 50]) == 30.0


def test_volume_multiple_and_spike():
    assert m.volume_multiple(100.0, 40.0) == 2.5
    assert m.is_spike(2.5) is True
    assert m.is_spike(2.0) is True
    assert m.is_spike(1.99) is False
    assert m.is_spike(0.0) is False


def test_build_row_is_pure_and_complete():
    r = m.build_row(code="588000", name="科创50ETF", current=120.0,
                    previous=45.0, history=[40, 42, 38, 41, 39],
                    is_2026_focus=True, share_change_yi=0.4)
    assert r.amount_change_yi == 75.0
    assert r.amount_change_pct > 0
    assert r.amount_ma_yi == 40.0
    assert r.volume_multiple == 3.0
    assert r.is_spike is True
    assert r.is_2026_focus is True
    d = r.as_dict()
    assert "volume_multiple" in d and "share_change_yi" in d


def test_divergence_is_explicit_in_observation():
    """放量但份额未增时，汇总文案必须点明「不能推出加仓」。"""
    rows = [
        m.build_row(code="510300", name="A", current=100, previous=40,
                    history=[40] * 5, share_change_yi=0.0),
        m.build_row(code="588000", name="B", current=100, previous=40,
                    history=[40] * 5, is_2026_focus=True, share_change_yi=0.0),
    ]
    s = m.summarize("2026-11-09", rows)
    assert s.spike_count == 2
    assert s.focus_spike_count == 1
    assert s.is_signal is False          # 恒非信号
    assert "不能推出申购或加仓" in s.observation


def test_all_share_up_does_not_claim_signal():
    rows = [
        m.build_row(code="510300", name="A", current=100, previous=40,
                    history=[40] * 5, share_change_yi=60.0),
    ]
    s = m.summarize("2026-11-09", rows)
    assert "无法识别申购人" in s.observation
    assert "另按三重共振条件判定" in s.observation
    assert s.is_signal is False


def test_anomaly_report_states_turnover_and_share_change():
    hot = m.build_row(
        code="588000", name="科创50ETF华夏（华夏科创50ETF）",
        current=30, previous=10, history=[10] * 5, share_change_yi=1.2,
    )
    report = m.anomaly_report([hot], 2)
    assert report["alert"] is True
    line = report["lines"][0]
    assert line.startswith("科创50ETF华夏 放量 3.0 倍")
    assert "成交额 30.0 亿" in line and "前日 10.0 亿" in line
    assert "份额变化 +1.2 亿" in line
    assert "亿份" not in line
    doubled = m.build_row(
        code="510300", name="沪深300ETF",
        current=19, previous=9, history=[10] * 5, share_change_yi=-0.4,
    )
    both = m.anomaly_report([doubled], 2)
    assert both["alert"] is False
    assert "环比" in both["lines"][0]
    quiet = m.build_row(
        code="510050", name="上证50ETF",
        current=11, previous=10, history=[10] * 5, share_change_yi=0.1,
    )
    none = m.anomaly_report([quiet], 2)
    assert none["alert"] is False
    assert none["lines"] == ["没有 ETF 达到 2 倍放量"]


def test_volume_alert_uses_existing_channels_without_trade_language():
    from ntm import db
    from ntm.alert import dispatch_volume, volume_message
    db.init_db()
    text = volume_message("2026-11-10", [
        "科创50ETF 放量 2.3 倍（成交额 40.0 亿，前日 17.0 亿，环比 +135%），份额变化 +1.1 亿",
    ])
    for word in ("买入", "卖出", "国家队加仓", "加仓信号"):
        assert word not in text
    result = dispatch_volume("2026-11-10", [text.split("\n", 1)[-1]])
    assert any(item["channel"] == "inbox" and item["status"] == "sent" for item in result)
    again = dispatch_volume("2026-11-10", [text.split("\n", 1)[-1]])
    assert any(item["status"] == "skipped" for item in again)


def test_no_spike_observation():
    rows = [m.build_row(code="510300", name="A", current=40, previous=40,
                        history=[40] * 5)]
    s = m.summarize("2026-11-09", rows)
    assert s.spike_count == 0
    assert "量能整体平稳" in s.observation


def test_no_trading_language_in_output():
    """对外输出文案（caveat / observation）不得出现任何买卖/仓位指令措辞。

    内部 docstring 中「不定义买入信号」属合规声明，不纳入本项检查范围。
    """
    rows = [m.build_row(code="510300", name="A", current=100, previous=40,
                        history=[40] * 5)]
    s = m.summarize("2026-11-09", rows)
    text = m.MOMENTUM_CAVEAT + s.observation + s.caveat
    for forbidden in ["买入信号", "卖出信号", "建仓", "加仓信号", "目标价", "止损", "买入", "卖出"]:
        assert forbidden not in text
