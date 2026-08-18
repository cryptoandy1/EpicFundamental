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
| 5 | GitHub: ядро и экосистема | ядро — активные разработчики в неделю по репо команды (stats API, для репо без stats — commits API); экосистема — новые репо с топиком проекта в неделю (Search API) | `GITHUB_TOKEN` желателен (иначе 60 req/час; Search — 10/мин); `github_topic` в projects.yaml |
| 6 | Авторитетные Twitter | ручная курация в projects.yaml → `twitter_curation` | руками |
| 7 | Разлоки + кошельки команды | ручные `unlock_events` (token.unlocks.app) или DefiLlama Pro; Etherscan для кошельков | `ETHERSCAN_API_KEY` (бесплатный) + адреса из Arkham |
| 8 | Динамика нод | адаптеры: ETH, Solana, NEAR, Avalanche, Cosmos-сети | история копится с запуска (ретроспективы у бесплатных API нет) |
| 9 | Топ-валидаторы бирж | моники валидаторов (Binance/OKX/...) → события на графике | — |
| 10 | Упоминания СМИ | GDELT (история с 2017) без PR-wire доменов + CryptoPanic | `CRYPTOPANIC_TOKEN` опционально |
| 11 | Smart money / потоки | **Nansen API** (план Pro): перпы Hyperliquid — позиции Smart Money по всем 9 монетам; `flow-intelligence` — нетто-потоки бирж и свежих кошельков (6 сетей); спот SM (SOL/AVAX/SEI). Бесплатный fallback — эвристика свежих кошельков (Etherscan) | `NANSEN_API_KEY` + поле `nansen` в projects.yaml; `ETHERSCAN_API_KEY` для fallback |
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
  Фактор без данных получает **нейтральный перцентиль 50** и участвует с полным весом: «не знаем»
  не должно ни помогать, ни вредить (иначе пропуск неявно приписывал бы монете её же средний
  уровень). Колонка «Покрытие» показывает долю веса скора, стоящую на реальных данных.
  Почти все факторы — моментум (среднее за 28 дней / за предыдущие 84): для очерёдности
  входа важно ускорение, а не масштаб. GitHub разделён на два фактора: `github_core_devs`
  (разработка ОТ команды — уникальные разработчики ядра в неделю, вес 0.5) и
  `github_ecosystem` (разработка НА платформе — новые репо по топику, окна 12/24 нед,
  вес 1.0; у малых экосистем с <15 репо за базу фактор пропускается как шум).

## Честные ограничения

- Скачивания Coinbase бесплатно недоступны — используется ранг в топ-чартах (на пиках
  маний Coinbase влетает в топ-10 общего топа, это и есть сигнал).
- Twitter-факторы (6, 13) без платного X API не автоматизируются — ручная курация/ввод.
- DefiLlama перенёс raises/emissions в Pro — бесплатная альтернатива: ручной ввод
  (данные публичны). При этом TVL/стейблкоины/DEX/комиссии по сетям бесплатны без
  ключа (коллектор defillama) — из них считаются факторы tvl_momentum и fees_momentum.
- Историю числа нод за прошлые годы бесплатные API не отдают — метрика копится
  с момента запуска (этим и занята ежедневная задача). Источники: RPC сетей —
  Solana `getVoteAccounts`, NEAR `validators`, Avalanche P-Chain, Cosmos LCD для TIA/SEI/INJ;
  для Sui, Aptos и Bittensor адаптеров пока нет.
- GitHub `/stats/contributors` для некоторых больших репо (anza-xyz/agave) отдаёт пустой
  ответ — коллектор сам уходит на листинг коммитов за 2 года. Топики экосистемы
  самоназначаемые: у малых сетей (Celestia, Sei) их единицы в год — фактор экосистемы
  там честно пропускается, пока не наберётся объём.
- **Nansen и нативные монеты.** Весь наш пул — нативные L1-монеты, а `tgm/flows`, `tgm/holders`,
  `token-screener` их не поддерживают («does not support native tokens»), поэтому дневной истории
  холдингов Smart Money по нашим монетам не существует. Спотовая когорта SM живёт только у
  SOL/AVAX/SEI. Сравнимый по всему пулу smart-money-сигнал даёт только `perp-screener` (Hyperliquid).
  В `flow-intelligence` сегмент `smart_trader` для плейсхолдера нативной монеты `0xeee…` возвращает
  одно и то же значение на всех EVM-сетях (баг Nansen) — коллектор его не пишет.
  Backtesting API (`token-screener/historical*`, `tgm/historical-token-flow-summary`) на нашем плане
  отвечает 404, поэтому снапшоты Nansen копятся вперёд, по одному в неделю.
  Вкладка Chains веб-приложения (активные адреса, транзакции) в API не отдаётся.
- **Кредиты Nansen — разовый грант**, не месячный: один прогон обновления стоит ~27 кредитов
  (перпы 2 + киты 9 + потоки 6 + спот 10). Предохранители: `NANSEN_MIN_INTERVAL_DAYS` (по умолчанию 7)
  и `NANSEN_MIN_CREDITS` (100) — ниже резерва коллектор останавливается. Остаток виден на «Обзоре рынка».
- pytrends неофициальный и капризный: при 429 подождите и перезапустите
  `backfill --collector trends --collector btc_trends`.

## Регулярный сбор

Два скрипта в корне + задачи в Планировщике Windows (созданы 2026-08-18):

| Задача | Когда | Что делает | Время / цена |
|--------|-------|------------|--------------|
| `update_daily.ps1` | при пробуждении компьютера, иначе в 18:00 | цены и капитализация, TVL/комиссии/стейблкоины/DEX, число валидаторов, ранг Coinbase, **лёгкая часть Nansen** (перп-скринер + flow-intelligence), экспорт JSON и **публикация сайта** | ~6 мин, 8 кредитов Nansen |
| `update_weekly.ps1` | воскресенье при пробуждении, иначе в 19:00 | всё из ежедневного + GitHub (ядро и экосистема), Google Trends, СМИ (GDELT), разлоки, кошельки, Discord, **полная часть Nansen** (киты + спотовая когорта SM), экспорт и публикация | ~25 мин, 27 кредитов |

Зачем разделение: факторы лесенки считаются как среднее снапшотов за 28 дней, поэтому
ежедневный сбор даёт 28 точек вместо 4 и делает их устойчивыми к случайному дню. Дорогие
справочные панели (киты, спот) обновляются раз в неделю. Расход — ~75 кредитов в неделю.

Коллектор Nansen сам следит за ритмом (`NANSEN_MIN_INTERVAL_DAYS` = 1,
`NANSEN_FULL_INTERVAL_DAYS` = 7), поэтому лишний запуск ничего не потратит: вернёт «пропуск».

Публикация включена в обе задачи: после экспорта запускается `deploy.ps1` (force-push в
`gh-pages`). Собрать без публикации — `.\update_daily.ps1 -NoDeploy`.
Логи: `backend/logs/daily.log`, `backend/logs/weekly.log` (в git не попадают).

**Запуск привязан к пробуждению компьютера.** У обеих задач два триггера: событие
«система вышла из сна» (журнал System, Power-Troubleshooter, EventID 1) с задержкой
3 и 8 минут соответственно — и запасное время 18:00 / 19:00 на случай, если компьютер
вообще не засыпал. Плюс `StartWhenAvailable` — догон, если машина была выключена.

Чтобы триггеров не набежало по нескольку прогонов, скрипты сами проверяют отметку
`backend/logs/*.stamp`:

* ежедневный — **ровно один прогон в календарный день**: первый сработавший триггер
  собирает данные, остальные в этот день выходят сразу;
* недельный — **один прогон в неделю, по воскресеньям**: запускается, если сегодня
  воскресенье и с прошлого раза прошло ≥ 6 дней; в остальные дни ждёт воскресенья, а
  если воскресенье пропущено целиком (компьютер не включали) — догоняет на 8-й день.

Игнорировать отметку и собрать принудительно: `.\update_daily.ps1 -Force`.
В воскресенье задачи не мешают друг другу: ежедневная делает лёгкую часть Nansen,
недельная — тяжёлую (интервалы лёгкой и полной части проверяются независимо).

Управление задачами:
```powershell
Get-ScheduledTask -TaskName "EpicFundamental*"                    # статус
Start-ScheduledTask -TaskName "EpicFundamental - ежедневный сбор"  # прогнать сейчас
Unregister-ScheduledTask -TaskName "EpicFundamental*"             # удалить
```

БД переносится простым копированием `backend/data/epicfundamental.db` (или миграция на
Postgres — модели на SQLAlchemy).

## Тесты

```powershell
cd backend
.venv\Scripts\python -m pytest tests -q
```
