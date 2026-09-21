# Яндекс 360: массовая отправка от имени группы

CLI-скрипт выдаёт пользователям право отправлять письма от имени почтового адреса группы. Администратор получает список рассылок в CSV, указывает UID сотрудников и подтверждает применение. Для каждой уникальной пары «рассылка + пользователь» выполняется отдельный POST, результат сохраняется в журнал и CSV-отчёт.

**Python 3.9+; внешние библиотеки, pip и виртуальное окружение не нужны.**

## Порядок работы и роль

**GET групп → выбор `emailId` → проверка → POST → проверка в Почте.** Сначала получите группы; используйте `emailId` найденной рассылки, а не обычный `id` группы.

В `mail_behalf.py` задана роль `mail_list_half_sender` для сценария отправки **от имени группы**. Приведённая ниже публичная документация POST описывает `mail_list_sender` — право писать **на адрес рассылки**. Это разные задачи; заменять роль автоматически нельзя.

`mail_list_half_sender` не описана в указанной публичной схеме. Команды ниже сохраняют роль проекта; перед массовой выдачей проверьте её поддержку на тестовой группе и при отказе уточните её у Яндекса. HTTP 204 сам по себе не заменяет проверку отправки в Почте.

## Публичная документация

1. [Просмотр списка групп](https://yandex.ru/dev/api360/doc/ru/ref/GroupService/GroupService_List)
2. [Изменение списка разрешенных отправителей рассылок на группы и подразделения](https://yandex.ru/dev/api360/doc/ru/mailing-list/post-mailing-list)


## Как устроен процесс

1. GET собирает все страницы справочника групп. Удалённые группы и группы без адреса/`emailId` пропускаются; CLI показывает их количество.
2. Скрипт создаёт CSV и сопутствующий журнал с организацией, операцией и снимком групп.
3. CLI остаётся открытым и ждёт, пока администратор заполнит CSV.
4. После Enter скрипт проверяет весь CSV, убирает повторяющиеся пары и повторно получает группы для проверки выбранных адресатов.
5. CLI показывает организацию, действие, число групп, пользователей и POST. Администратор вводит контрольную фразу, например `GRANT 12`.
6. POST выполняются последовательно; каждый результат сохраняется сразу. В конце выводится сводка и путь к отчёту.

REST не означает «один пользователь на запрос». У POST поле `subjects` является массивом. В этом проекте выбран один UID на запрос для понятной диагностики и продолжения частично выполненной операции; поддержка пакетной отправки именно для `mail_list_half_sender` здесь не заявляется.

## Подготовка

Нужны:

- Python 3.9 или новее с HTTPS-поддержкой;
- ID организации;
- OAuth-токен администратора с доступом к нужной организации;
- UID сотрудников — числовые ID, не логины и не email.

Для чтения групп используется разрешение `directory:read_groups` ([справочник групп](https://yandex.ru/dev/api360/doc/ru/ref/GroupService/)). Для изменения подготовьте оба scope: `ya360_admin:mail_write_mail_list_permissions` и `ya360_admin:mail_read_mail_list_permissions`. После изменения scopes получите новый токен. Проверьте доступность нужной роли на тестовой группе.

Положите `mail_behalf.py` в рабочую папку. Все CSV, журналы и временные файлы скрипт создаёт рядом с собой, независимо от текущего каталога терминала. `--file` не позволяет выбрать другую папку.

## Быстрый запуск

```bash
python3 mail_behalf.py --org-id 1234567
```

В Bash команда выше выполняется как написано. Для Zsh и PowerShell:

```zsh
python3 mail_behalf.py --org-id 1234567
```

```powershell
py -3 mail_behalf.py --org-id 1234567
```

Этот интерактивный запуск сначала выполняет GET, затем ждёт заполнения CSV, повторяет GET и запрашивает подтверждение перед POST. Если токен не задан в переменной окружения `OAUTH_TOKEN`, скрипт запросит его скрытым вводом. ID организации можно передать через `--org-id`, переменную `ORG_ID` или интерактивный ввод. Токен не записывается в файлы; аргумента `--token` и чтения `.env` нет.

После выгрузки откройте указанный CSV. **Редактируйте только третью колонку `uids`.** UID вводятся в CSV, а в CLI вводятся Enter и контрольная фраза.

| name | emailId | uids |
| --- | --- | --- |
| Поддержка | 1130000000000001 | 1130000000000101,1130000000000102 |
| Продажи | 1130000000000002 | 1130000000000103 |
| Новости | 1130000000000003 | |

Это условные идентификаторы, а не данные реальной организации.

Физический формат файла:

```csv
name;emailId;uids
Поддержка;1130000000000001;1130000000000101,1130000000000102
Продажи;1130000000000002;1130000000000103
Новости;1130000000000003;
```

- Кодировка — UTF-8 с BOM; разделитель колонок — **точка с запятой**. Запятая разделяет UID внутри третьей колонки.
- В Excel импортируйте через «Данные → Из текста/CSV», задав всем колонкам тип **Текст**. Excel может необратимо округлить длинные числовые ID при обычном открытии двойным щелчком. Кавычки CSV сами по себе этого не предотвращают.
- Сохраняйте CSV в UTF-8 с разделителем `;`, не XLSX. Допускается UTF-8 без BOM.
- Пустой `uids` означает «ничего не менять», **не отзыв прав**. Ненужные строки можно удалить.
- Пробелы вокруг UID допускаются. Пустые элементы (`123,,456`), завершающая запятая, дроби и экспонента отклоняются.
- Повторы одной пары, включая повторы в разных строках, выполняются один раз. Один UID в разных группах — разные операции.
- `emailId` — идентификатор рассылки; поле `id` группы для POST не подходит.
- Заголовки, `name` и `emailId` менять нельзя. Названия, похожие на формулы таблиц, при экспорте получают защитный апостроф; его нужно сохранить.

Сохраните и закройте файл, вернитесь в CLI, нажмите Enter и проверьте сводку. Только после точного ввода `GRANT N` или `REVOKE N` начнётся изменение прав. План уже прочитан в память: последующие изменения CSV не меняют подтверждённый запуск.

## Раздельные этапы и проверка

Это основной порядок для пошагового запуска: сначала `export` (GET), затем редактирование CSV, `apply --dry-run` (проверка через GET) и только после успешной проверки — `apply` (GET и подтверждение перед POST). Ниже команды Bash/Zsh; в PowerShell используйте `py -3` вместо `python3`.

Выгрузить CSV и завершить процесс:

```bash
python3 mail_behalf.py export --org-id 1234567 --file groups.csv
```

После редактирования проверить файл без POST:

```bash
python3 mail_behalf.py apply --org-id 1234567 --file groups.csv --dry-run
```

Применить или продолжить прерванное выполнение:

```bash
python3 mail_behalf.py apply --org-id 1234567 --file groups.csv
```

`--dry-run` обращается к GET, проверяет формат и выбранные группы, но не подтверждает существование UID, права пользователя на изменение или поддержку роли. Такие ошибки может вернуть POST. Скрипт не выгружает весь справочник сотрудников и не требует соответствующего дополнительного scope.

Существующие CSV и журналы не перезаписываются при экспорте. Для новой выгрузки выберите новое имя или запускайте без `--file` — имя будет сформировано автоматически.

## Отзыв доступа

Для отзыва создайте отдельную выгрузку, заполните UID, затем примените её:

```bash
python3 mail_behalf.py export --org-id 1234567 --file revoke.csv --operation revoke
python3 mail_behalf.py apply --org-id 1234567 --file revoke.csv --operation revoke --dry-run
python3 mail_behalf.py apply --org-id 1234567 --file revoke.csv --operation revoke
```

Операция закрепляется в журнале при экспорте. Нельзя применить журнал `grant` как `revoke`. `overwrite` не используется. Скрипт не меняет членство в группах.

## Журнал, отчёт и продолжение

Для `groups.csv` создаются:

| Файл | Назначение |
| --- | --- |
| `groups.state.json` | Организация, операция, снимок групп и результат каждой пары. Нужен для `apply`. Не редактировать вручную. |
| `groups.results.csv` | Последний отчёт по парам из текущего CSV. Обновляется после применения, включая обработанный Ctrl+C. |
| `groups.lock` | Защита от одновременной обработки того же CSV. Удаляется при обычном завершении. |

Статусы:

| Статус | Что означает |
| --- | --- |
| `success` | Получен HTTP 204. При `apply` эта пара пропускается. |
| `failed` | Получена определённая ошибка HTTP. После исправления причины `apply` повторит пару. |
| `unknown` | Ответ потерян, получен 5xx/408 или неожиданный успешный статус. Запрос мог примениться. |
| `pending` | Намерение сохранено до POST, но финальный результат не записан, например из-за прерывания. Также требует проверки. |
| `not_started` | До пары не дошла очередь. |

Для `pending`/`unknown` автоматическое продолжение блокируется. Проверьте фактический результат в Почте. Если повторное выполнение осознанно необходимо:

```bash
python3 mail_behalf.py apply --org-id 1234567 --file groups.csv --retry-uncertain
```

Контрольная фраза всё равно требуется. Скрипт не обещает «ровно один раз» при потере ответа: без серверного ключа идемпотентности это нельзя гарантировать.

Журнал — история конкретной операции, а не постоянная синхронизация прав. Если после успешного запуска права изменили извне либо вы хотите повторно выдать доступ после отзыва, создайте **новую выгрузку с новым журналом**. Успешные записи старого журнала не перепроверяются по текущим разрешениям. Не запускайте одну кампанию одновременно через разные копии CSV: блокировка действует только на одно имя файла.

После аварийного завершения процесса может остаться `.lock`. Убедитесь, что прежний процесс завершён, затем удалите только соответствующий `.lock`. Не удаляйте `.state.json` для обхода неопределённого результата. При повреждении/утрате журнала восстановите его из резервной копии либо вручную сверяйте фактические права перед новой кампанией.

Сохраняется результат каждого запроса, но после принудительного завершения процесса CSV-отчёт может отстать от журнала. `.state.json` — основной источник состояния. Утилита не является транзакцией: успешные изменения не откатываются при ошибке следующей пары.

## Ошибки и параметры

GET повторяется до трёх раз после первой попытки при сетевых сбоях и HTTP 429/500/502/503/504. Числовой `Retry-After` до 60 секунд учитывается; при большем значении или HTTP-дате запуск останавливается. POST автоматически не повторяется.

При 401/403/429, сетевой неопределённости или 5xx обработка останавливается, остальные пары остаются `not_started`. Другие ошибки отдельных пар, например 400/404, сохраняются, выполнение продолжается. Ошибки формата CSV обнаруживаются **до первого POST**.

| Параметр | Значение |
| --- | --- |
| `--timeout 30` | Тайм-аут HTTP, 1–300 секунд; не общий лимит времени кампании. |
| `--delay 0.3` | Пауза между POST, 0–60 секунд. Это настройка темпа, не гарантия соблюдения всех серверных квот. |
| `--help` | Справка CLI. |

Коды завершения: `0` — этап завершён успешно (включая отсутствие UID); `1` — ошибка подготовки/файла или отмена контрольной фразой; `2` — применение завершилось с ошибками, неопределёнными или невыполненными парами; `130` — Ctrl+C/закрытие стандартного ввода.

При 401 проверьте токен, при 403 — административные права и scopes, при 400 — UID и поддержку роли, при 404 — организацию и `emailId`. При 429 выдержите серверный интервал перед повторным `apply`. Тела ошибок сервера и токен намеренно не попадают в лог.

CSV и журналы содержат сведения о группах и пользователях. `.gitignore` исключает рабочие файлы, а исходный `manual.md` с примером организации — из новых добавлений в Git. Уже отслеживаемые файлы `.gitignore` не скрывает: перед публикацией проверьте `git status` и состав коммита. Не добавляйте токен в код или историю команд.


## Порядок работы вручную

**Сначала GET групп и проверка выбранной рассылки, только затем POST.** Ниже — независимые от Python-скрипта команды. Выполняйте этапы в одной сессии терминала. Для Bash/Zsh дополнительно нужны `curl` и `python3`; PowerShell использует встроенный `Invoke-RestMethod`.

### PowerShell

#### 1. Токен и GET всех страниц групп

Подходит для Windows PowerShell 5.1 и PowerShell 7. Не используйте здесь Bash-конструкцию `<<JSON`. Имя `curl` в Windows PowerShell 5.1 может быть псевдонимом другого клиента, поэтому ниже выбран нативный HTTP-клиент.

```powershell
$OrgId = '1234567'
$SecureToken = Read-Host 'OAuth token' -AsSecureString
$Token = [System.Net.NetworkCredential]::new('', $SecureToken).Password
if ([string]::IsNullOrWhiteSpace($Token)) { throw 'Токен не введён' }
$Headers = @{ Authorization = "OAuth $Token" }
$GroupsReady = $false
$Groups = @()
$Page = 1

do {
    $Uri = "https://api360.yandex.net/directory/v1/org/$OrgId/groups?page=$Page&perPage=100"
    $Response = Invoke-RestMethod -Method Get -Uri $Uri -Headers $Headers -ErrorAction Stop
    if ($null -eq $Response.groups -or $null -eq $Response.pages) {
        throw 'В ответе GET отсутствуют groups/pages'
    }
    $Groups += @($Response.groups)
    $Page++
} while ($Page -le [int]$Response.pages)

$GroupsOrgId = $OrgId
$GroupsReady = $true
$Groups | Where-Object { -not $_.removed -and $_.email -and $_.emailId } |
    Select-Object name, email, emailId | Format-Table -AutoSize
```

#### 2. Выбор рассылки и GET текущих разрешений

Замените `EmailId` значением **из GET**, сверив название и адрес группы. `UserId` — UID сотрудника из карточки администратора. Все числа в примерах демонстрационные.

```powershell
$EmailId = '1130000000000001'
$UserId = '1130000000000101'
$PermissionsReady = $false
if (-not $GroupsReady -or $GroupsOrgId -ne $OrgId) {
    throw 'Сначала выполните GET групп'
}
$Selected = @($Groups | Where-Object {
    [string]$_.emailId -eq $EmailId -and -not $_.removed -and $_.email
})
if ($Selected.Count -ne 1) { throw 'Рассылка не найдена однозначно в GET' }
$Selected | Select-Object name, email, emailId | Format-List

$BaseUrl = "https://cloud-api.yandex.net/v1/admin/org/$OrgId/mail-lists/$EmailId"
$Before = Invoke-RestMethod -Method Get -Uri "$BaseUrl/permissions" -Headers $Headers -ErrorAction Stop
$Before | ConvertTo-Json -Depth 30
$PermissionsTarget = "$OrgId/$EmailId"
$PermissionsReady = $true
```

#### 3. POST после проверки

`grant` выдаёт право одному сотруднику. Для отзыва пройдите GET-этапы заново и замените его на `revoke`.

```powershell
$Operation = 'grant'
if (-not $PermissionsReady -or $PermissionsTarget -ne "$OrgId/$EmailId") {
    throw 'Сначала выполните GET разрешений выбранной рассылки'
}
if ($Operation -notin @('grant', 'revoke')) { throw 'Допустимы grant или revoke' }
if ($UserId -notmatch '^[1-9][0-9]*$' -or $OrgId -notmatch '^[1-9][0-9]*$') {
    throw 'Ожидаются числовые UID и ID организации'
}
$Body = @{
    role_actions = @(@{
        type = $Operation
        roles = @('mail_list_half_sender')
        subjects = @(@{
            type = 'user'
            id = [uint64]$UserId
            org_id = [uint64]$OrgId
        })
    })
} | ConvertTo-Json -Depth 10
$Body
$Expected = "$Operation $EmailId $UserId"
if ((Read-Host "Для POST введите $Expected") -cne $Expected) { throw 'Отменено' }
$PermissionsReady = $false
$Request = @{
    Method = 'Post'
    Uri = "$BaseUrl/update-permissions"
    Headers = $Headers
    ContentType = 'application/json'
    Body = $Body
    ErrorAction = 'Stop'
}
Invoke-RestMethod @Request
Write-Host 'POST завершён без HTTP-ошибки. Проверьте результат в Почте.'
```

Пустой ответ нормален: документированный статус успеха — HTTP 204. При HTTP-ошибке `-ErrorAction Stop` останавливает блок.

### Bash

#### 1. Токен и организация

```bash
OAUTH_TOKEN=''
read -r -s -p 'OAuth token: ' OAUTH_TOKEN
printf '\n'
ORG_ID='1234567'
```

Затем выполните общие шаги 2–4 ниже.

### Zsh

#### 1. Токен и организация

```zsh
OAUTH_TOKEN=''
read -r -s 'OAUTH_TOKEN?OAuth token: '
printf '\n'
ORG_ID='1234567'
```

Затем выполните общие шаги 2–4 ниже. В Zsh `read -p` означает чтение из сопроцесса и может вызвать `no coprocess`.

### Общие шаги для Bash и Zsh

Эти блоки одинаково работают в обеих оболочках. Функции останавливают этап через `return` при ошибке, не закрывая терминал.

#### 2. GET всех страниц групп

Ответы сохраняются в новом временном каталоге. Просмотрите группы и найдите нужный почтовый адрес.

```sh
get_groups() {
  GROUPS_READY=''
  [ -n "$OAUTH_TOKEN" ] || { printf 'Токен не введён\n' >&2; return 1; }
  command -v python3 >/dev/null || return 1
  WORK_DIR=$(mktemp -d) || return 1
  PAGE=1
  while :; do
    HTTP_CODE=$(curl --silent --show-error --connect-timeout 15 --max-time 60 \
      --header "Authorization: OAuth ${OAUTH_TOKEN}" \
      --output "$WORK_DIR/groups-$PAGE.json" --write-out '%{http_code}' \
      "https://api360.yandex.net/directory/v1/org/${ORG_ID}/groups?page=${PAGE}&perPage=100") || return 1
    [ "$HTTP_CODE" = '200' ] || { printf 'GET: HTTP %s\n' "$HTTP_CODE" >&2; return 1; }
    python3 -m json.tool "$WORK_DIR/groups-$PAGE.json" || return 1
    PAGES=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d["groups"],list); p=d["pages"]; assert type(p) is int and p>=0; print(p)' "$WORK_DIR/groups-$PAGE.json") || return 1
    [ "$PAGE" -ge "$PAGES" ] && break
    PAGE=$((PAGE + 1))
  done
  GROUPS_ORG_ID="$ORG_ID"
  GROUPS_READY=1
}
get_groups
```

#### 3. Выбор рассылки и GET текущих разрешений

Задайте `EMAIL_ID` по результату GET, а `USER_ID` — по карточке сотрудника.

```sh
EMAIL_ID='1130000000000001'
USER_ID='1130000000000101'

get_permissions() {
  PERMISSIONS_READY=''
  [ "$GROUPS_READY" = '1' ] && [ "$GROUPS_ORG_ID" = "$ORG_ID" ] || {
    printf 'Сначала выполните GET групп\n' >&2; return 1;
  }
  python3 - "$WORK_DIR" "$EMAIL_ID" <<'PY'
import json, pathlib, sys
groups = []
for path in pathlib.Path(sys.argv[1]).glob("groups-*.json"):
    groups.extend(json.loads(path.read_text())["groups"])
selected = [g for g in groups if str(g.get("emailId")) == sys.argv[2]
            and not g.get("removed") and g.get("email")]
if len(selected) != 1:
    sys.exit("Рассылка не найдена однозначно в GET")
print(json.dumps(selected[0], ensure_ascii=False, indent=2))
PY
  [ "$?" -eq 0 ] || return 1
  BASE_URL="https://cloud-api.yandex.net/v1/admin/org/${ORG_ID}/mail-lists/${EMAIL_ID}"
  HTTP_CODE=$(curl --silent --show-error --connect-timeout 15 --max-time 60 \
    --header "Authorization: OAuth ${OAUTH_TOKEN}" \
    --output "$WORK_DIR/permissions-before.json" --write-out '%{http_code}' \
    "$BASE_URL/permissions") || return 1
  [ "$HTTP_CODE" = '200' ] || { printf 'GET: HTTP %s\n' "$HTTP_CODE" >&2; return 1; }
  python3 -m json.tool "$WORK_DIR/permissions-before.json" || return 1
  PERMISSIONS_TARGET="$ORG_ID/$EMAIL_ID"
  PERMISSIONS_READY=1
}
get_permissions
```

#### 4. POST после проверки

```sh
OPERATION='grant'

change_permission() {
  [ "$PERMISSIONS_READY" = '1' ] && [ "$PERMISSIONS_TARGET" = "$ORG_ID/$EMAIL_ID" ] || {
    printf 'Сначала выполните GET разрешений выбранной рассылки\n' >&2; return 1;
  }
  python3 - "$OPERATION" "$USER_ID" "$ORG_ID" > "$WORK_DIR/request.json" <<'PY'
import json, re, sys
operation, uid, org = sys.argv[1:]
if operation not in ("grant", "revoke"):
    sys.exit("Допустимы grant или revoke")
if any(not re.fullmatch(r"[1-9][0-9]*", v) or int(v) > 2**64-1 for v in (uid, org)):
    sys.exit("Ожидаются положительные целые ID в диапазоне uint64")
json.dump({"role_actions": [{"type": operation, "roles": ["mail_list_half_sender"],
          "subjects": [{"type": "user", "id": int(uid), "org_id": int(org)}]}]},
          sys.stdout, indent=2)
PY
  [ "$?" -eq 0 ] || return 1
  cat "$WORK_DIR/request.json"
  printf '\nДля POST введите %s %s %s: ' "$OPERATION" "$EMAIL_ID" "$USER_ID"
  read -r CONFIRM || return 1
  [ "$CONFIRM" = "$OPERATION $EMAIL_ID $USER_ID" ] || { printf 'Отменено\n'; return 1; }
  PERMISSIONS_READY=''
  HTTP_CODE=$(curl --silent --show-error --connect-timeout 15 --max-time 60 \
    --request POST \
    --header "Authorization: OAuth ${OAUTH_TOKEN}" \
    --header 'Content-Type: application/json' \
    --data-binary "@$WORK_DIR/request.json" \
    --output "$WORK_DIR/post-response.txt" --write-out '%{http_code}' \
    "$BASE_URL/update-permissions") || {
      printf 'Ответ не получен. Проверьте результат до повтора POST.\n' >&2; return 1;
    }
  printf 'HTTP %s\n' "$HTTP_CODE"
  [ "$HTTP_CODE" = '204' ]
}
change_permission
```

Ожидается HTTP 204 с пустым телом. Ответ сохранён в `$WORK_DIR/post-response.txt`. Для отзыва повторите GET-этапы, установите `OPERATION='revoke'` и вызовите `change_permission`. Автоматического повтора POST нет.

### Проверка после POST

1. Повторите GET `/permissions` и сравните ответ с исходным, если API возвращает нужную роль. Отображение `mail_list_half_sender` в этом ответе публично не описано: отсутствие роли в GET само по себе не доказывает отсутствие права.
2. Под указанным сотрудником обновите веб-Почту или войдите заново.
3. После `grant` проверьте выбор адреса группы в поле «От кого», отправку на контролируемый адрес и фактического отправителя у получателя.
4. После `revoke` проверьте, что отправка от имени группы недоступна, в том числе из старого черновика.

GET текущих разрешений — дополнительная проверка, а не подтверждение поддержки роли. [Метод чтения разрешений приведён в справке Яндекса](https://yandex.ru/support/yandex-360/business/admin/ru/mail/mailbox-management/mailing-list).

**Python-скрипт сейчас выполняет GET групп, но не GET разрешений.** Ручная последовательность выше включает оба. Успех GET не гарантирует успех POST.

Очистите переменные с токеном после работы.

PowerShell:

```powershell
Remove-Variable Token, SecureToken, Headers, Request -ErrorAction SilentlyContinue
```

Bash и Zsh:

```sh
unset OAUTH_TOKEN
```

### Частые ошибки ручных команд

| Симптом | Что проверить |
| --- | --- |
| `read: -p: no coprocess` | Вы в Zsh; используйте отдельную команду ввода для Zsh. |
| HTTP 401 | Токен введён, действителен и передаётся в заголовке OAuth. |
| HTTP 403 | Административный доступ и scopes приложения. |
| HTTP 400 | Роль, ID и JSON. Не подменяйте роль другой только ради успешного ответа. |
| HTTP 404 | Организация и `emailId` из GET; обычный `id` группы не подходит. |
| HTTP 429 | Выдержите серверный интервал, затем проверьте состояние перед повтором. |
| Тайм-аут / 5xx после POST | Запрос мог примениться. Сначала проверьте результат в Почте. |


## Разработка и локальная проверка

```bash
python3 -B -m unittest -v test_mail_behalf.py
python3 -B mail_behalf.py --help
```

Тесты используют стандартные `unittest`/`unittest.mock`, подменяют HTTP и создают временные файлы только в папке проекта. Они не обращаются к организации и не выдают права.

