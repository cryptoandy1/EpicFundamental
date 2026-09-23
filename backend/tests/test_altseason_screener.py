"""Ворота входа (altseason) и watch-list скринера — на фикстурах, без сети."""
from __future__ import annotations

from app.collectors.altseason import AltseasonCollector, alts_beating_btc_share
from app.models import MARKET, Metric


def _coin(cid: str, symbol: str, change: float | None, price: float = 100.0) -> dict:
    return {
        "id": cid,
        "symbol": symbol,
        "current_price": price,
        "price_change_percentage_30d_in_currency": change,
    }


def test_alts_beating_btc_excludes_stables_and_wrappers():
    markets = [
        _coin("bitcoin", "btc", 10.0),
        _coin("solana", "sol", 25.0),        # обгоняет
        _coin("near", "near", 30.0),         # обгоняет
        _coin("aptos", "apt", 5.0),          # отстаёт
        _coin("tether", "usdt", 0.1, 1.0),   # стейблкоин из списка
        _coin("some-new-stable", "usdx", 0.3, 1.001),  # стейблкоин по эвристике
        _coin("wrapped-bitcoin", "wbtc", 10.2),        # обёртка
        _coin("celestia", "tia", None),      # нет 30д-изменения — не считаем
    ]
    share, beating, total = alts_beating_btc_share(markets)
    assert (beating, total) == (2, 3)  # sol, near из sol/near/apt
    assert share == 66.7


def test_alts_beating_btc_without_bitcoin_returns_none():
    assert alts_beating_btc_share([_coin("solana", "sol", 25.0)]) == (None, 0, 0)


def test_altseason_collector_writes_both_metrics(session, fake_http):
    http = fake_http(
        {
            "/global": {"data": {"market_cap_percentage": {"btc": 59.4, "eth": 12.0}}},
            "/coins/markets": [
                _coin("bitcoin", "btc", 10.0),
                _coin("solana", "sol", 25.0),
                _coin("aptos", "apt", 5.0),
            ],
        }
    )
    report = AltseasonCollector(session, http).backfill()

    dominance = session.query(Metric).filter_by(project_id=MARKET, metric="btc_dominance_pct").one()
    assert dominance.value == 59.4
    alts = session.query(Metric).filter_by(project_id=MARKET, metric="alts_beating_btc_30d_pct").one()
    assert alts.value == 50.0
    assert "59.4" in report

    # идемпотентность: повторный прогон в тот же день не плодит точки
    AltseasonCollector(session, http).backfill()
    assert session.query(Metric).filter_by(metric="btc_dominance_pct").count() == 1


def test_altseason_survives_dead_global_endpoint(session, fake_http):
    """Метрики независимы: падение /global не должно ронять вторую метрику."""
    http = fake_http({"/coins/markets": [_coin("bitcoin", "btc", 1.0), _coin("solana", "sol", 9.0)]})
    report = AltseasonCollector(session, http).backfill()
    assert "ошибка" in report
    assert session.query(Metric).filter_by(metric="alts_beating_btc_30d_pct").count() == 1


def test_screener_watchlist_keeps_coins_that_fail_filters(session, fake_http, monkeypatch):
    """HYPE/ZEC выпадают по FDV/MC и капе — watch-list обязан их сохранить с причиной."""
    from app import screener

    monkeypatch.setattr(
        screener,
        "load_config",
        lambda: {
            "screener": {
                "min_market_cap": 100_000_000,
                "max_market_cap": 50_000_000_000,
                "min_volume_24h": 10_000_000,
                "max_fdv_mc_ratio": 3.0,
                "categories": ["layer-1"],
                "always_include": ["hyperliquid", "zcash"],
            }
        },
    )
    http = fake_http(
        {
            "'category': 'layer-1'": [
                {
                    "id": "solana", "name": "Solana", "symbol": "sol",
                    "market_cap": 12e9, "fully_diluted_valuation": 14e9, "total_volume": 3e9,
                },
            ],
            "'ids': 'hyperliquid,zcash'": [
                {   # FDV/MC ≈ 3.8 — фильтр не проходит
                    "id": "hyperliquid", "name": "Hyperliquid", "symbol": "hype",
                    "market_cap": 24e9, "fully_diluted_valuation": 90e9, "total_volume": 1e9,
                },
                {   # проходит всё после подъёма потолка до $50B
                    "id": "zcash", "name": "Zcash", "symbol": "zec",
                    "market_cap": 25.7e9, "fully_diluted_valuation": 25.7e9, "total_volume": 1.1e9,
                },
            ],
        }
    )
    rows = {c["coingecko_id"]: c for c in screener.run_screener(session, http)}

    assert set(rows) == {"solana", "hyperliquid", "zcash"}
    assert rows["hyperliquid"]["categories"] == "watchlist"
    assert "FDV/MC 3.75 > 3.0" in rows["hyperliquid"]["reason"]
    assert "проходит все фильтры" in rows["zcash"]["reason"]
    assert rows["solana"]["categories"] == "layer-1"
