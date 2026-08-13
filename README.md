# EpicFundamental

Инструмент фундаментального анализа крипто-проектов для стратегии «лесенки» на булл ран:
последовательный вход в проекты по очереди их роста, выход по рыночным индикаторам-пикам.
Отслеживает 14 факторов по пулу монет, строит дашборды и ранжирует очередность входа.

Только **бесплатные** источники данных; платные (X API, Nansen, DefiLlama Pro) подключаются
опционально через env-переменные. Приоритет — **бэкфилл всей доступной истории**.

## Быстрый старт

```powershell
# Backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app sync        # projects.yaml -> БД
.venv\Scripts\python -m app backfill    # вся доступная история (займёт время)
.venv\Scripts\python -m app serve       # API на :8000

# Frontend (второй терминал)
cd frontend
npm install
npm run dev                             # дашборд на http://localhost:3000
```

## Команды CLI

```
python -m app sync                выгрузить config/projects.yaml в БД
python -m app backfill            вся доступная история по всем коллекторам
python -m app backfill --project solana --collector trends
python -m app update              инкрементальное обновление (для планировщика/сервера)
python -m app screen              скринер кандидатов (ф.3)
python -m app ladder              композитный скор «лесенки» в консоль
python -m app list-collectors     список коллекторов
python -m app serve               API-сервер
python -m app export              статический экспорт API в JSON (frontend/public/data)
```

## 14 факторов → где данные

| # | Фактор | Источник | Что нужно |
|---|--------|----------|-----------|
| 1 | Google Trends BTC (пик = слив) | pytrends, вся история | — |
| 2 | «Скачивания» Coinbase | ранг в App Store (прокси) + web.archive.org | — |
| 3 | Пул монет | скринер CoinGecko → ручное утверждение | фильтры в projects.yaml |
| 4 | Возраст / фандинг | CoinGecko genesis + ручные `funding_rounds` | 5 мин на монету (cryptorank.io) или `DEFILLAMA_API_KEY` |
| 5 | GitHub vs аналоги | GitHub API, вся история коммитов | `GITHUB_TOKEN` желателен (иначе 60 req/час) |
| 6 | Авторитетные Twitter | ручная курация в projects.yaml → `twitter_curation` | руками |
| 7 | Разлоки + кошельки команды | ручные `unlock_events` (token.unlocks.app) или DefiLlama Pro; Etherscan для кошельков | `ETHERSCAN_API_KEY` (бесплатный) + адреса из Arkham |
| 8 | Динамика нод | адаптеры: ETH, Solana, NEAR, Avalanche, Cosmos-сети | история копится с запуска (ретроспективы у бесплатных API нет) |
| 9 | Топ-валидаторы бирж | моники валидаторов (Binance/OKX/...) → события на графике | — |
| 10 | Упоминания СМИ | GDELT (история с 2017) без PR-wire доменов + CryptoPanic | `CRYPTOPANIC_TOKEN` опционально |
| 11 | Smart money | эвристика свежих кошельков (Etherscan) | `ETHERSCAN_API_KEY` + `token_contracts` |
| 12 | Discord по существу | автоэкспорт scripts/discord_export.py + классификация Claude API | `DISCORD_TOKEN` + `ANTHROPIC_API_KEY` (иначе эвристика) |
| 13 | Активность CEO | ручной ввод в config/manual_metrics.yaml | руками (X API платный) |
| 14 | Google Trends по проекту | pytrends, вся история | — |
| + | Экономика сети: TVL, стейблкоины, DEX-объёмы, комиссии | DefiLlama, бесплатные эндпоинты, вся история | — |

## Env-переменные (все опциональны)

Кладите в `backend/.env` (шаблон — `backend/.env.example`; файл в .gitignore) или в окружение:

```
GITHUB_TOKEN         github.com/settings/tokens — лимит 5000 req/час вместо 60
ETHERSCAN_API_KEY    etherscan.io (бесплатный) — кошельки команды + smart money
ANTHROPIC_API_KEY    классификация Discord-сообщений (модель: CLASSIFY_MODEL, по умолчанию claude-opus-5)
CRYPTOPANIC_TOKEN    cryptopanic.com/developers/api (бесплатный тариф)
DEFILLAMA_API_KEY    DefiLlama Pro — авто-фандинг и авто-разлоки вместо ручного ввода
```

## Discord-экспорт (ф.12)

1. Вступите своим аккаунтом в Discord-серверы монет пула (инвайты — `discord_invite`
   в projects.yaml).
2. Положите `DISCORD_TOKEN` в backend/.env (как достать — комментарий в .env.example;
   учтите: экспорт юзер-токеном формально против ToS Discord).
3. Распакуйте [DiscordChatExporter.Cli.win-x64](https://github.com/Tyrrrz/DiscordChatExporter/releases)
   в `tools/DiscordChatExporter/` (не в git).
4. `cd backend && .venv\Scripts\python scripts\discord_export.py` — экспорт последних
   120 дней основных каналов, затем `python -m app backfill --collector discord`.

## Roadmap

- **Автообновление**: задача в планировщике Windows (ежедневно `update` + deploy.ps1) —
  решили включить позже, когда надоест запускать руками.
- Нативные переводы в коллекторе кошельков (сейчас только ERC-20 `tokentx` — фонд,
  двигающий нативный AVAX, не виден) и не-EVM адаптеры (Solscan/NearBlocks/Mintscan)
  для SOL/NEAR/TIA/SUI/APT/TAO.
- Бэктест лесенки: as-of скоринг по накопленной истории + симуляция ротации vs HODL BTC.
- Алерты выхода (Trends BTC ≥90 / Coinbase в топ-10) в Telegram.
- Ручной ввод unlock_events / funding_rounds по всем монетам пула (или DefiLlama Pro).

## Публикация на GitHub Pages

Дашборд публикуется как статический сайт: `python -m app export` снимает JSON-снапшоты
всех эндпоинтов в `frontend/public/data`, `STATIC_EXPORT=1 next build` собирает сайт,
который читает эти снапшоты вместо живого API.

```powershell
.\deploy.ps1     # export -> build -> push в ветку gh-pages
```

Сайт: https://cryptoandy1.github.io/EpicFundamental/ — данные обновляются только при
повторном запуске deploy.ps1 (после `python -m app update`).

## Дашборды

- **Обзор рынка** — сигнал выхода: Google Trends BTC с перцентиль-алертом (≥90 = «пик,
  сливаем»), ранг Coinbase в App Store, цена BTC.
- **Пул монет** — утверждённый пул + кандидаты скринера.
- **Проект** — цена с оверлеем разлоков и раундов, эмиссия, GitHub vs аналоги, ноды +
  биржевые валидаторы, СМИ, Trends, выводы кошельков команды, Discord, CEO, курация Twitter.
- **Лесенка** — ранжирование пула по композитному скору (веса в projects.yaml → scoring).

## Честные ограничения

- Скачивания Coinbase бесплатно недоступны — используется ранг в топ-чартах (на пиках
  маний Coinbase влетает в топ-10 общего топа, это и есть сигнал).
- Twitter-факторы (6, 13) без платного X API не автоматизируются — ручная курация/ввод.
- DefiLlama перенёс raises/emissions в Pro — бесплатная альтернатива: ручной ввод
  (данные публичны). При этом TVL/стейблкоины/DEX/комиссии по сетям бесплатны без
  ключа (коллектор defillama) — из них считаются факторы tvl_momentum и fees_momentum.
- Историю числа нод за прошлые годы бесплатные API не отдают — метрика копится
  с момента запуска (поэтому запускайте `update` регулярно).
- pytrends неофициальный и капризный: при 429 подождите и перезапустите
  `backfill --collector trends --collector btc_trends`.

## Регулярный сбор

Пока вручную/по желанию: `python -m app update`. Когда решите вкладываться и поднимете
сервер — тот же `update` в cron (раз в день достаточно), БД перенесётся простым копированием
`backend/data/epicfundamental.db` (или миграция на Postgres — модели на SQLAlchemy).

## Тесты

```powershell
cd backend
.venv\Scripts\python -m pytest tests -q
```
