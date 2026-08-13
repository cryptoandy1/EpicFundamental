"""Тесты коллекторов на записанных фикстурах (сеть не нужна)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.collectors.market import MarketCollector
from app.collectors.mentions import MentionsCollector
from app.collectors.nodes import NodesCollector
from app.models import Event, Mention, Metric


def _binance_kline(day_ms: int, close: float) -> list:
    return [day_ms, "0", "0", "0", str(close), "10", day_ms + 86_399_999, "1000", 1, "0", "0", "0"]


def test_market_collector_price_and_cap(session, project, fake_http):
    base_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    http = fake_http(
        {
            "binance.com": [_binance_kline(base_ms + i * 86_400_000, 100 + i) for i in range(3)],
            "market_chart": {
                "market_caps": [[base_ms, 1_000_000.0]],
                "total_volumes": [[base_ms, 50_000.0]],
            },
            "coins/testcoin": {"genesis_date": "2023-05-01", "categories": ["layer-1"]},
        }
    )
    report = MarketCollector(session, http).backfill(project)

    prices = session.query(Metric).filter_by(metric="price_usd").all()
    assert len(prices) == 3
    assert prices[0].value == 100
    assert session.query(Metric).filter_by(metric="market_cap").count() == 1
    assert project.genesis_date == datetime(2023, 5, 1)
    assert "price_usd: 3" in report

    # идемпотентность: повторный прогон не плодит дубли
    MarketCollector(session, http).backfill(project)
    assert session.query(Metric).filter_by(metric="price_usd").count() == 3


def test_nodes_collector_cosmos_exchange_validators(session, project, fake_http):
    http = fake_http(
        {
            "staking/v1beta1/validators": {
                "validators": [
                    {"description": {"moniker": "Binance Node"}},
                    {"description": {"moniker": "Random Validator"}},
                    {"description": {"moniker": "OKX Earn"}},
                ],
                "pagination": {"next_key": None},
            }
        }
    )
    report = NodesCollector(session, http).backfill(project)
    assert "3 активных" in report

    node_count = session.query(Metric).filter_by(metric="node_count").one()
    assert node_count.value == 3
    exchange = session.query(Metric).filter_by(metric="exchange_validators_count").one()
    assert exchange.value == 2  # binance + okx

    events = session.query(Event).filter_by(type="validator").all()
    assert {e.title.split(":")[0] for e in events} == {"binance", "okx"}

    # повторный прогон: событие «подключился» не дублируется
    NodesCollector(session, http).backfill(project)
    assert session.query(Event).filter_by(type="validator").count() == 2


def test_mentions_collector_filters_pr_domains(session, project, fake_http):
    http = fake_http(
        {
            "timelinevolraw": {
                "timeline": [
                    {
                        "data": [
                            {"date": "20240101T000000Z", "value": 5},
                            {"date": "20240102T000000Z", "value": 7},
                        ]
                    }
                ]
            },
            "artlist": {
                "articles": [
                    {
                        "url": "https://coindesk.com/a",
                        "domain": "coindesk.com",
                        "title": "Real news",
                        "seendate": "20240101T120000Z",
                    },
                    {
                        "url": "https://prnewswire.com/b",
                        "domain": "prnewswire.com",
                        "title": "Paid PR",
                        "seendate": "20240101T130000Z",
                    },
                ]
            },
        }
    )
    MentionsCollector(session, http).backfill(project)

    assert session.query(Metric).filter_by(metric="media_mentions").count() > 0
    mentions = {m.domain: m.is_pr for m in session.query(Mention).all()}
    assert mentions["coindesk.com"] is False
    assert mentions["prnewswire.com"] is True


def test_defillama_collector(session, project, fake_http):
    from app.collectors.defillama import DefiLlamaCollector

    http = fake_http(
        {
            "historicalChainTvl": [
                {"date": 1700000000, "tvl": 100.0},
                {"date": 1700086400, "tvl": 120.0},
            ],
            "stablecoincharts": [
                {"date": "1700000000", "totalCirculatingUSD": {"peggedUSD": 50.0, "peggedEUR": 5.0}},
            ],
            "overview/dexs": {"totalDataChart": [[1700000000, 7.0]]},
            "overview/fees": {"totalDataChart": [[1700000000, 0.0]]},  # всё нули — пропуск
        }
    )
    report = DefiLlamaCollector(session, http).backfill(project)

    assert session.query(Metric).filter_by(metric="chain_tvl_usd").count() == 2
    assert session.query(Metric).filter_by(metric="stablecoins_usd").one().value == 55.0
    assert session.query(Metric).filter_by(metric="dex_volume_usd").count() == 1
    assert session.query(Metric).filter_by(metric="chain_fees_usd").count() == 0
    assert "chain_tvl_usd: 2" in report
    assert "нулевой ряд" in report

    # идемпотентность
    DefiLlamaCollector(session, http).backfill(project)
    assert session.query(Metric).filter_by(metric="chain_tvl_usd").count() == 2


def test_scoring_percentiles(session, project):
    """Композитный скор: у монеты с бОльшими коммитами и меньшим навесом скор выше."""
    from app.models import Project
    from app.scoring import compute_ladder

    other = Project(id="othercoin", name="Othercoin", symbol="OTH", chain="near")
    session.add(other)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for week in range(6):
        ts = now - timedelta(days=7 * week)
        session.add(Metric(project_id="testcoin", metric="github_commits_week", ts=ts, value=100))
        session.add(Metric(project_id="othercoin", metric="github_commits_week", ts=ts, value=5))
    session.commit()

    rows = {r["project"]: r for r in compute_ladder(session)}
    assert rows["testcoin"]["score"] is not None
    assert rows["testcoin"]["score"] > rows["othercoin"]["score"]
    gh = rows["testcoin"]["factors"]["github_activity"]
    assert gh["percentile"] == 100.0


def test_discord_heuristic():
    from app.collectors.social import _heuristic_substantive

    assert not _heuristic_substantive("gm")
    assert not _heuristic_substantive("GM GM!!!")
    assert not _heuristic_substantive("good project")
    assert not _heuristic_substantive("🚀🚀🚀")
    assert _heuristic_substantive("How does data availability sampling work on light nodes?")
    assert _heuristic_substantive("Why did the validator set shrink after the upgrade?")
