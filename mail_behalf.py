#!/usr/bin/env python3
"""Yandex 360 group send-as permissions. Python standard library only."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
ROLE = "mail_list_half_sender"
FIELDS = ["name", "emailId", "uids"]
RESULT_FIELDS = ["name", "emailId", "uid", "operation", "status", "http_status"]


class AppError(Exception):
    pass


class ApiError(AppError):
    def __init__(self, status=None):
        self.status = status
        super().__init__(f"HTTP {status}" if status else "Сетевая ошибка или тайм-аут")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identifier(value, label):
    value = str(value).strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value) or int(value) > 2**64 - 1:
        raise AppError(f"{label}: ожидается положительный целый ID без округления и экспоненты")
    return value


def display_name(value):
    # Prevent formulas when CSV is opened in a spreadsheet; name is informational.
    value = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if value.lstrip().startswith(("=", "+", "-", "@")):
        value = "'" + value
    return value


def atomic_write(path, write):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="",
                                         dir=path.parent, delete=False) as stream:
            temp = Path(stream.name)
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def save_state(path, state):
    atomic_write(path, lambda stream: json.dump(state, stream, ensure_ascii=False, indent=2))


def write_csv(path, fields, rows):
    def write(stream):
        writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    atomic_write(path, write)


class Client:
    def __init__(self, token, org, timeout=30):
        self.token, self.org, self.timeout = token, org, timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, url, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Authorization": "OAuth " + self.token, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        # Only GET is retried; POST outcome may already have been committed.
        for attempt in range(4 if method == "GET" else 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    if method == "POST":
                        if response.status != 204:
                            raise ApiError(response.status)
                        return None
                    if response.status != 200:
                        raise ApiError(response.status)
                    result = json.loads(response.read().decode("utf-8"))
                    if not isinstance(result, dict):
                        raise AppError("GET вернул JSON неожиданного формата")
                    return result
            except urllib.error.HTTPError as exc:
                status = exc.code
                retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
                exc.close()
                if method == "GET" and status in (429, 500, 502, 503, 504) and attempt < 3:
                    # Long / date-form Retry-After stops this run instead of retrying early.
                    if retry_after and (not retry_after.isdigit() or int(retry_after) > 60):
                        raise ApiError(status) from None
                    time.sleep(int(retry_after) if retry_after else 2**attempt)
                    continue
                raise ApiError(status) from None
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                if method == "GET" and attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise ApiError() from None
            except (ValueError, UnicodeError):
                raise AppError("GET вернул некорректный JSON") from None

    def groups(self):
        found, seen, page = {}, set(), 1
        while True:
            result = self.request("GET", f"https://api360.yandex.net/directory/v1/org/{self.org}"
                                  f"/groups?page={page}&perPage=100")
            batch, pages = result.get("groups"), result.get("pages")
            if not isinstance(batch, list) or type(pages) is not int or pages < 0:
                raise AppError("GET: отсутствуют корректные groups/pages")
            if result.get("page") != page and not (page == 1 and pages == 0 and not batch):
                raise AppError("GET: неверный номер страницы")
            if pages < page and not (page == 1 and pages == 0 and not batch):
                raise AppError("GET: непоследовательная пагинация")
            if not batch and page < pages:
                raise AppError("GET: пустая промежуточная страница; повторите выгрузку")
            for group in batch:
                if not isinstance(group, dict):
                    raise AppError("GET: некорректная группа")
                gid = identifier(group.get("id"), "group.id")
                if gid in seen:
                    raise AppError("GET: группа повторилась между страницами; повторите выгрузку")
                seen.add(gid)
                if group.get("removed") is True or not group.get("email") or not group.get("emailId"):
                    continue
                eid = identifier(group["emailId"], "emailId")
                if eid in found:
                    raise AppError("GET: повтор emailId")
                found[eid] = {"name": display_name(group.get("name", "")), "email": group["email"]}
            if page >= pages:
                return found, len(seen) - len(found)
            page += 1
            if page > 100000:
                raise AppError("Превышен предел страниц")

    def grant(self, email_id, uid, operation):
        payload = {"role_actions": [{"type": operation, "roles": [ROLE],
                   "subjects": [{"type": "user", "id": uid, "org_id": self.org}]}]}
        return self.request("POST", f"https://cloud-api.yandex.net/v1/admin/org/{self.org}"
                            f"/mail-lists/{email_id}/update-permissions", payload)


def read_plan(path, inventory):
    plan, seen, duplicates = [], set(), 0
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";", strict=True)
        if reader.fieldnames != FIELDS:
            raise AppError("Заголовок CSV должен быть name;emailId;uids, разделитель — ;")
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise AppError(f"Строка {number}: нужно ровно три столбца")
            if not any(value.strip() for value in row.values()):
                continue
            eid = identifier(row["emailId"], f"Строка {number}, emailId")
            if eid not in inventory or row["name"] != inventory[eid]["name"]:
                raise AppError(f"Строка {number}: name/emailId изменены или отсутствуют в выгрузке")
            if not row["uids"].strip():
                continue
            for raw_uid in row["uids"].split(","):
                uid = identifier(raw_uid, f"Строка {number}, UID")
                pair = (eid, uid)
                if pair in seen:
                    duplicates += 1
                else:
                    seen.add(pair)
                    plan.append(pair)
    return plan, duplicates


def key(pair):
    return ":".join(pair)


def export_file(client, path, state_path, operation):
    if path.exists() or state_path.exists():
        raise AppError("CSV или журнал уже существует. Используйте apply либо новое имя --file")
    groups, skipped = client.groups()
    state = {"version": 1, "org_id": client.org, "operation": operation, "role": ROLE,
             "groups": groups, "results": {}}
    save_state(state_path, state)
    write_csv(path, FIELDS, ({"name": group["name"], "emailId": eid, "uids": ""}
                            for eid, group in groups.items()))
    print(f"Выгружено рассылок: {len(groups)}. Пропущено удалённых/без почтового адреса: {skipped}.")
    print(f"CSV: {path}")
    return state


def load_state(path, org, operation):
    with path.open(encoding="utf-8-sig") as stream:
        state = json.load(stream)
    if (not isinstance(state, dict) or state.get("version") != 1 or state.get("org_id") != org
            or state.get("operation") != operation or state.get("role") != ROLE
            or not isinstance(state.get("groups"), dict) or not isinstance(state.get("results"), dict)):
        raise AppError("Журнал не соответствует организации, операции или версии скрипта")
    for item in state["results"].values():
        if not isinstance(item, dict) or item.get("status") not in ("pending", "unknown", "success", "failed"):
            raise AppError("Журнал повреждён")
    return state


def report(path, plan, state):
    rows = []
    for pair in plan:
        result = state["results"].get(key(pair), {})
        rows.append({"name": state["groups"][pair[0]]["name"], "emailId": pair[0],
                     "uid": pair[1], "operation": state["operation"],
                     "status": result.get("status", "not_started"), "http_status": result.get("http_status", "")})
    write_csv(path, RESULT_FIELDS, rows)
    counts = Counter(row["status"] for row in rows)
    print("Итог: " + ", ".join(f"{status}={count}" for status, count in sorted(counts.items())))
    print(f"Отчёт: {path}")
    return 0 if all(row["status"] == "success" for row in rows) else 2


def apply_file(client, path, state_path, state, dry_run=False, retry_uncertain=False, delay=0.3):
    plan, duplicates = read_plan(path, state["groups"])
    if not plan:
        print("Нет UID для обработки. POST не отправлялись.")
        return 0
    current, _ = client.groups()
    for eid, _ in plan:
        if eid not in current or current[eid] != state["groups"][eid]:
            raise AppError("Состав/название/адрес выбранных групп изменились. Сделайте новую выгрузку")
    todo, uncertain = [], 0
    for pair in plan:
        status = state["results"].get(key(pair), {}).get("status")
        if status == "success":
            continue
        if status in ("pending", "unknown"):
            uncertain += 1
        todo.append(pair)
    print(f"Организация: {client.org}; операция: {state['operation']}; роль: {ROLE}")
    print(f"Групп: {len({p[0] for p in plan})}; уникальных UID: {len({p[1] for p in plan})}; "
          f"пар: {len(plan)}; дублей исключено: {duplicates}.")
    print(f"Уже выполнено: {len(plan)-len(todo)}; POST запланировано: {len(todo)}; неопределённых: {uncertain}.")
    if dry_run:
        print("Проверка завершена. POST не отправлялись; существование UID проверит API при применении.")
        return 0
    if uncertain and not retry_uncertain:
        raise AppError("Есть pending/unknown. Проверьте результат вручную; для осознанного повтора — --retry-uncertain")
    if todo:
        phrase = f"{state['operation'].upper()} {len(todo)}"
        if input(f"Для выполнения введите {phrase}: ").strip() != phrase:
            raise AppError("Операция отменена: подтверждение не совпало")
    try:
        for index, pair in enumerate(todo, 1):
            # Persist intent BEFORE sending: interruption cannot silently cause a replay.
            state["results"][key(pair)] = {"status": "pending", "http_status": ""}
            save_state(state_path, state)
            stop = False
            try:
                client.grant(*pair, state["operation"])
                result = {"status": "success", "http_status": 204}
            except ApiError as exc:
                ambiguous = exc.status is None or exc.status >= 500 or 200 <= exc.status < 300 or exc.status == 408
                result = {"status": "unknown" if ambiguous else "failed", "http_status": exc.status or ""}
                stop = ambiguous or exc.status in (401, 403, 429)
            state["results"][key(pair)] = result
            save_state(state_path, state)
            print(f"[{index}/{len(todo)}] emailId={pair[0]} UID={pair[1]}: {result['status']} {result['http_status']}")
            if stop:
                print("Выполнение остановлено. Проверьте ошибку перед продолжением через apply.")
                break
            if index < len(todo):
                time.sleep(delay)
    finally:
        # State is authoritative even if report writing fails or process is killed.
        report(path.with_suffix(".results.csv"), plan, state)
    return 0 if all(state["results"].get(key(p), {}).get("status") == "success" for p in plan) else 2


def parser():
    p = argparse.ArgumentParser(description="Отправка от имени группы Яндекс 360: выгрузка → CSV → подтверждение → POST")
    p.add_argument("mode", nargs="?", default="interactive", choices=["interactive", "export", "apply"])
    p.add_argument("--org-id", default=os.environ.get("ORG_ID"))
    p.add_argument("--file", help="Имя CSV в папке скрипта")
    p.add_argument("--operation", choices=["grant", "revoke"], default="grant")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--retry-uncertain", action="store_true")
    p.add_argument("--timeout", type=float, default=30)
    p.add_argument("--delay", type=float, default=0.3)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    lock = None
    acquired = False
    try:
        if not 1 <= args.timeout <= 300 or not 0 <= args.delay <= 60:
            raise AppError("--timeout: 1–300 секунд; --delay: 0–60 секунд")
        if args.mode == "apply" and not args.file:
            raise AppError("Для apply требуется --file")
        if args.mode == "export" and args.dry_run:
            raise AppError("--dry-run доступен в interactive/apply")
        org = identifier(args.org_id or input("ID организации: "), "org_id")
        filename = args.file or f"groups_{org}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}.csv"
        path = (ROOT / filename).resolve()
        if path.parent != ROOT or path.suffix.lower() != ".csv":
            raise AppError("--file должен указывать CSV непосредственно в папке скрипта")
        state_path = path.with_suffix(".state.json")
        lock = path.with_suffix(".lock")
        try:
            with lock.open("x") as stream:
                stream.write(str(os.getpid()))
            acquired = True
        except FileExistsError:
            raise AppError("Найден .lock: возможно, CSV уже обрабатывается. См. README") from None
        token = os.environ.get("OAUTH_TOKEN", "").strip() or getpass.getpass("OAuth-токен (ввод скрыт): ").strip()
        if not token or any(ch.isspace() for ch in token):
            raise AppError("OAuth-токен пуст или содержит пробельные символы")
        client = Client(token, org, args.timeout)
        if args.mode == "apply":
            state = load_state(state_path, org, args.operation)
        else:
            state = export_file(client, path, state_path, args.operation)
            if args.mode == "export":
                return 0
            print("Откройте CSV, заполните uids через запятую. Сохраните и закройте файл.")
            input("После редактирования нажмите Enter...")
        return apply_file(client, path, state_path, state, args.dry_run, args.retry_uncertain, args.delay)
    except (KeyboardInterrupt, EOFError):
        print("\nОстановлено. Сохранённый CSV можно продолжить через apply.", file=sys.stderr)
        return 130
    except (AppError, OSError, ValueError, csv.Error):
        # Never print arbitrary HTTP bodies, credentials, or exception payloads.
        exc = sys.exc_info()[1]
        message = str(exc) if isinstance(exc, AppError) else "Ошибка чтения/записи или формата файла. Проверьте CSV, журнал и доступ к папке."
        print("Ошибка: " + message, file=sys.stderr)
        return 1
    finally:
        if acquired:
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
