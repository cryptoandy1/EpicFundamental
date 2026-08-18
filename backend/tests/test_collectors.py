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
    """Композитный скор: у монеты с растущим числом активных разработчиков скор выше.
    Фактор — моментум (4 недели / предыдущие 12), а не абсолют: othercoin крупнее, но сдувается."""
    from app.models import Project
    from app.scoring import compute_ladder

    other = Project(id="othercoin", name="Othercoin", symbol="OTH", chain="near")
    session.add(other)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for week in range(16):
        ts = now - timedelta(days=7 * week + 1)
        recent = week < 4
        session.add(
            Metric(project_id="testcoin", metric="github_active_devs_week", ts=ts, value=20 if recent else 10)
        )
        session.add(
            Metric(project_id="othercoin", metric="github_active_devs_week", ts=ts, value=50 if recent else 100)
        )
    session.commit()

    rows = {r["project"]: r for r in compute_ladder(session)}
    assert rows["testcoin"]["score"] is not None
    assert rows["testcoin"]["score"] > rows["othercoin"]["score"]
    gh = rows["testcoin"]["factors"]["github_core_devs"]
    assert gh["percentile"] == 100.0
    assert gh["value"] == 2.0  # 20/10
    assert rows["othercoin"]["factors"]["github_core_devs"]["value"] == 0.5


def test_momentum_min_base_total(session, project):
    """Редкий счётчик (мало событий в базовом окне) -> моментум None, фактор исключён."""
    from app.scoring import _momentum

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for week in range(36):
        ts = now - timedelta(days=7 * week + 1)
        session.add(
            Metric(project_id="testcoin", metric="github_eco_new_repos_week", ts=ts, value=1 if week % 6 == 0 else 0)
        )
    session.commit()
    # база 24 недели содержит 4 события — ниже порога 15
    assert _momentum(session, "testcoin", "github_eco_new_repos_week", 84, 168, min_base_total=15) is None
    assert _momentum(session, "testcoin", "github_eco_new_repos_week", 84, 168, min_base_total=0) is not None


def _week_unix(days_ago: int) -> int:
    from app.collectors.github_activity import week_start

    ts = week_start(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_ago))
    return int(ts.replace(tzinfo=timezone.utc).timestamp())


def test_github_collector_stats_dedups_devs_and_skips_bots(session, fake_http):
    """stats-путь: один логин в двух репо за неделю = 1 разработчик; бот отброшен; нулевые недели пишутся."""
    from app.collectors.github_activity import GithubCollector
    from app.models import Project

    project = Project(id="two", name="Two", symbol="TWO", github_repos='["org/a", "org/b"]')
    session.add(project)
    session.commit()
    w1, w0 = _week_unix(21), _week_unix(14)  # завершённые недели (текущая не пишется)
    stats_a = [
        {"author": {"login": "alice", "type": "User"}, "weeks": [{"w": w1, "c": 3}, {"w": w0, "c": 0}]},
        {"author": {"login": "dependabot[bot]", "type": "Bot"}, "weeks": [{"w": w1, "c": 9}, {"w": w0, "c": 9}]},
    ]
    stats_b = [
        {"author": {"login": "alice", "type": "User"}, "weeks": [{"w": w1, "c": 2}, {"w": w0, "c": 0}]},
        {"author": {"login": "bob", "type": "User"}, "weeks": [{"w": w1, "c": 1}, {"w": w0, "c": 0}]},
    ]
    http = fake_http(
        {
            "org/a/stats/contributors": stats_a,
            "org/b/stats/contributors": stats_b,
            "org/a/releases": [],
            "org/b/releases": [],
            "repos/org/a": {"stargazers_count": 10, "forks_count": 1, "open_issues_count": 0},
            "repos/org/b": {"stargazers_count": 5, "forks_count": 1, "open_issues_count": 0},
        }
    )
    GithubCollector(session, http).backfill(project)

    devs = {m.ts: m.value for m in session.query(Metric).filter_by(metric="github_active_devs_week").all()}
    commits = {m.ts: m.value for m in session.query(Metric).filter_by(metric="github_commits_week").all()}
    ts1 = datetime.fromtimestamp(w1, tz=timezone.utc).replace(tzinfo=None)
    ts0 = datetime.fromtimestamp(w0, tz=timezone.utc).replace(tzinfo=None)
    assert devs[ts1] == 2  # alice (в обоих репо) + bob; бот не считается
    assert commits[ts1] == 6  # 3 + 2 + 1, без 9+9 бота
    assert devs[ts0] == 0 and commits[ts0] == 0  # нулевая неделя записана явно
    assert session.query(Metric).filter_by(metric="github_stars").one().value == 15


def test_github_collector_fallback_to_commits_api(session, project, fake_http):
    """stats вернула [] -> листинг /commits (2 страницы), автор -> неделя."""
    from app.collectors.github_activity import GithubCollector, week_start

    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=15)).strftime("%Y-%m-%dT12:00:00Z")
    d0 = (now - timedelta(days=8)).strftime("%Y-%m-%dT12:00:00Z")
    page1 = [
        {"author": {"login": "carol", "type": "User"}, "commit": {"author": {"date": d0, "email": "c@x"}}}
    ] * 100
    page2 = [
        {"author": {"login": "dave", "type": "User"}, "commit": {"author": {"date": d1, "email": "d@x"}}},
        {"author": None, "commit": {"author": {"date": d1, "email": "anon@x"}}},
        {"author": {"login": "ci[bot]", "type": "Bot"}, "commit": {"author": {"date": d1, "email": "b@x"}}},
    ]
    http = fake_http(
        {
            "stats/contributors": [],
            "'page': 1": page1,
            "'page': 2": page2,
            "org/repo/releases": [],
            "repos/org/repo": {"stargazers_count": 1, "forks_count": 0, "open_issues_count": 0},
        }
    )
    report = GithubCollector(session, http).backfill(project)
    assert "commits API" in report

    devs = {m.ts: m.value for m in session.query(Metric).filter_by(metric="github_active_devs_week").all()}
    commits = {m.ts: m.value for m in session.query(Metric).filter_by(metric="github_commits_week").all()}
    ts0 = week_start((now - timedelta(days=8)).replace(tzinfo=None))
    ts1 = week_start((now - timedelta(days=15)).replace(tzinfo=None))
    assert devs[ts0] == 1 and commits[ts0] == 100
    assert devs[ts1] == 2 and commits[ts1] == 2  # dave + анонимный e-mail; бот отброшен
    assert len(devs) >= 100  # покрыты все 104 недели окна (нулями)


def test_github_collector_no_data_keeps_existing_series(session, project, fake_http):
    """Оба пути пусты -> недельные ряды не трогаем (старые данные не перетираются), снапшот пишем."""
    from app.collectors.github_activity import GithubCollector

    old_ts = datetime(2024, 1, 7)
    session.add(Metric(project_id="testcoin", metric="github_commits_week", ts=old_ts, value=42))
    session.commit()
    http = fake_http(
        {
            "stats/contributors": [],
            "org/repo/commits": [],
            "org/repo/releases": [],
            "repos/org/repo": {"stargazers_count": 7, "forks_count": 0, "open_issues_count": 0},
        }
    )
    report = GithubCollector(session, http).backfill(project)
    assert "НЕ записаны" in report
    assert session.query(Metric).filter_by(metric="github_commits_week").one().value == 42
    assert session.query(Metric).filter_by(metric="github_active_devs_week").count() == 0
    assert session.query(Metric).filter_by(metric="github_stars").one().value == 7


def test_github_ecosystem_collector(session, project, fake_http, monkeypatch):
    """Новые репо по топику: недельные точки из total_count, update — только хвост, идемпотентно."""
    from app.collectors import github_ecosystem
    from app.collectors.github_ecosystem import GithubEcosystemCollector

    monkeypatch.setattr(github_ecosystem, "topic_of", lambda p: "testcoin")
    http = fake_http({"search/repositories": {"total_count": 7}})
    report = GithubEcosystemCollector(session, http).backfill(project)
    assert "topic:testcoin" in report
    rows = session.query(Metric).filter_by(metric="github_eco_new_repos_week").all()
    assert len(rows) == github_ecosystem.BACKFILL_WEEKS
    assert all(r.value == 7 for r in rows)
    assert all("topic:testcoin created:" in c for c in http.calls)
    # update: только последние недели, без дублей
    http2 = fake_http({"search/repositories": {"total_count": 9}})
    GithubEcosystemCollector(session, http2).update(project)
    assert len(http2.calls) == github_ecosystem.UPDATE_WEEKS
    assert (
        session.query(Metric).filter_by(metric="github_eco_new_repos_week").count()
        == github_ecosystem.BACKFILL_WEEKS
    )
    assert (
        session.query(Metric).filter_by(metric="github_eco_new_repos_week", value=9).count()
        == github_ecosystem.UPDATE_WEEKS
    )


def test_http_throttle_prefix_and_rate_limit():
    """Троттлинг по префиксу host+path (Search 2.1с) и ожидание по X-RateLimit-Reset."""
    import time as _t

    from app.collectors.base import Http

    assert Http._interval_key("https://api.github.com/search/repositories?q=x") == ("api.github.com/search", 2.1)
    assert Http._interval_key("https://api.github.com/repos/a/b")[1] == 0.8

    class R:
        def __init__(self, status, headers):
            self.status_code, self.headers = status, headers

    assert Http._rate_limit_wait(R(200, {})) is None
    assert Http._rate_limit_wait(R(403, {"X-RateLimit-Remaining": "5"})) is None
    wait = Http._rate_limit_wait(R(403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(_t.time() + 30)}))
    assert 25 <= wait <= 32
    far = R(429, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(_t.time() + 900)})
    assert Http._rate_limit_wait(far) == 90.0


def test_discord_heuristic():
    from app.collectors.social import _heuristic_substantive

    assert not _heuristic_substantive("gm")
    assert not _heuristic_substantive("GM GM!!!")
    assert not _heuristic_substantive("good project")
    assert not _heuristic_substantive("🚀🚀🚀")
    assert _heuristic_substantive("How does data availability sampling work on light nodes?")
    assert _heuristic_substantive("Why did the validator set shrink after the upgrade?")
