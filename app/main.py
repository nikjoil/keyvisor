from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from typing import Optional
from app.services.topvisor_api import TopvisorService
from app.services.word_keeper_api import WordKeeperService
from app.services.keysso_api import KeysSoService
from app.utils.excel_parser import parse_excel_file
from app.utils.yandex_regions import search_region_local

from pydantic import BaseModel, Field
import re
import json

app = FastAPI(title="KeyVisor Hybrid API", version="1.0-Mobile")


@app.get("/")
def health_check():
    return {"status": "ok", "mode": "Production Ready"}


@app.get("/utils/search-regions")
def util_search_regions(search_city: str):
    return {"status": "success", "data": search_region_local(search_city)}


@app.get("/test/check-wk")
async def test_wk(
    phrases_text: str = "пластиковые окна\nремонт окон",
    region_id: int = 213,
    wk_keep_project: bool = True,  # по умолчанию оставляем, чтобы ты видел в аккаунте
):
    clean_lines = split_phrases_text(phrases_text)
    if not clean_lines:
        return {"error": "Пустая строка"}

    wk = WordKeeperService()
    resp = await wk.check_volume(
        clean_lines,
        region_id,
        keep_project=wk_keep_project,
        with_meta=True,
        project_name=f"KV API TEST | geo={region_id} | n={len(clean_lines)}",
    )
    return {"status": "success", **resp}


@app.get("/test/wk/remove")
def test_wk_remove(project_id: str):
    wk = WordKeeperService()
    return wk.remove_project(project_id)


def split_phrases_text(text: str) -> list[str]:
    parts = re.split(r"[\r\n,;|]+", text)
    return [p.strip() for p in parts if p.strip()]


def _safe_str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, (int, float, bool)):
        return str(val).strip()
    try:
        return json.dumps(val, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(val).strip()


def _sanitize_phrase(s: str) -> str:
    s = s or ""
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ").replace(";", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_raw_item(raw, default_group: str = "General"):
    if raw is None:
        return None

    if isinstance(raw, str):
        p = _sanitize_phrase(raw)
        return {"phrase": p, "group": default_group} if p else None

    if not isinstance(raw, dict):
        p = _sanitize_phrase(_safe_str(raw))
        return {"phrase": p, "group": default_group} if p else None

    phrase = raw.get("phrase") or raw.get("word") or raw.get("keyword")
    group = raw.get("group") or raw.get("url") or default_group

    phrase_s = _sanitize_phrase(_safe_str(phrase))
    group_s = _sanitize_phrase(_safe_str(group)) or default_group

    if not phrase_s:
        return None

    out = dict(raw)
    out["phrase"] = phrase_s
    out["group"] = group_s
    return out


def _group_priority(group: str) -> int:
    # Manual Input > File > Keys.so
    if group == "Manual Input":
        return 30
    if isinstance(group, str) and group.startswith("[Keys.so]"):
        return 10
    return 20


def dedup_by_phrase(items: list[dict]) -> tuple[list[dict], int]:
    """Дедуп по фразе (как Topvisor)."""
    chosen: dict[str, dict] = {}
    dup_count = 0

    for it in items:
        p = it["phrase"]
        if p not in chosen:
            chosen[p] = it
            continue

        dup_count += 1
        if _group_priority(it.get("group", "")) > _group_priority(
            chosen[p].get("group", "")
        ):
            chosen[p] = it

    return list(chosen.values()), dup_count


class WKCheckRequest(BaseModel):
    phrases_text: str = Field(
        ..., description="Фразы: каждая с новой строки или через запятую/;"
    )
    region_id: int = Field(213, description="ID региона Яндекса (213 = Москва)")


@app.post("/harvester/start")
async def start_harvester(
    target_domain: str = Form(
        ..., description="Сайт проекта (для создания в Topvisor)"
    ),
    region_id: int = Form(
        ..., description="ID региона (например, 213 - Москва, 35 - Краснодар)"
    ),
    min_freq: int = Form(
        10, description="Минимальная точная частота. Если меньше - ключ удаляем."
    ),
    manual_text: Optional[str] = Form(
        None, description="Ключи списком (каждый с новой строки)"
    ),
    file: Optional[UploadFile] = File(None, description="Файл Excel / Webmaster"),
    use_keysso: bool = Form(True, description="Тянуть ли ключи с Keys.so?"),
    keysso_base: str = Form("msk", description="База Keys.so (msk - default)"),
    # ✅ Только это управление WK оставляем
    wk_keep_project: bool = Form(
        False,
        description="Сохранить проект в WordKeeper (не удалять после результата, для дебага в аккаунте)",
    ),
):
    status_log: list[str] = []
    stats = {
        "source_counts": {"manual": 0, "file": 0, "keysso": 0},
        "raw_total": 0,
        "normalized_total": 0,
        "unique_phrase_group": 0,
        "wk_requested_phrase_group": 0,
        "wk_requested_unique_phrases": 0,
        "wk_duplicates_before_wk": 0,
        "wk_returned": 0,
        "wk_missing": 0,
        "wk_project_id": None,
        "wk_project_name": None,
        "wk_keep_project": wk_keep_project,
        "wk_query_format": '"[!слово1 !слово2]"',
        "filter_min_freq": min_freq,
        "passed_filter_phrase_group": 0,
        "dup_phrase_in_passed_filter": 0,
        "topvisor_sent": 0,
        "topvisor_added": 0,
    }

    try:
        all_raw_data = []

        # 1) Manual
        if manual_text:
            lines = split_phrases_text(manual_text)
            txt_objs = [{"phrase": k, "group": "Manual Input"} for k in lines]
            all_raw_data.extend(txt_objs)
            stats["source_counts"]["manual"] = len(txt_objs)
            status_log.append(f"✅ Text: {len(txt_objs)}")

        # 2) File
        if file:
            file_data = await file.read()
            parsed = parse_excel_file(file_data)
            all_raw_data.extend(parsed)
            stats["source_counts"]["file"] = len(parsed)
            status_log.append(f"✅ File: {len(parsed)}")

        # 3) Keys.so
        if use_keysso:
            ks = KeysSoService()
            keys = ks.get_site_organic(target_domain, base=keysso_base)

            if keys and isinstance(keys[0], dict):
                all_raw_data.extend(keys)
                stats["source_counts"]["keysso"] = len(keys)
                status_log.append(f"✅ Keys.so: {len(keys)} (dict)")
            else:
                ks_objs = [{"phrase": k, "group": "Keys.so"} for k in keys]
                all_raw_data.extend(ks_objs)
                stats["source_counts"]["keysso"] = len(ks_objs)
                status_log.append(f"✅ Keys.so: {len(ks_objs)} (strings)")

        stats["raw_total"] = len(all_raw_data)
        status_log.append(f"ℹ️ Raw total: {stats['raw_total']}")

        # --- Normalize ---
        normalized = []
        dropped = 0
        for raw in all_raw_data:
            item = normalize_raw_item(raw)
            if item:
                normalized.append(item)
            else:
                dropped += 1

        stats["normalized_total"] = len(normalized)
        status_log.append(
            f"ℹ️ Normalized: {stats['normalized_total']} | dropped: {dropped}"
        )

        if not normalized:
            raise HTTPException(400, "❌ Нет валидных ключей после нормализации.")

        # --- Unique by (phrase, group) ---
        unique_map = {}
        for item in normalized:
            unique_map[(item["phrase"], item["group"])] = item
        unique_data = list(unique_map.values())

        stats["unique_phrase_group"] = len(unique_data)
        status_log.append(f"ℹ️ Unique (phrase+group): {stats['unique_phrase_group']}")

        # --- WORDKEEPER ---
        phrases_pg = [item["phrase"] for item in unique_data]
        stats["wk_requested_phrase_group"] = len(phrases_pg)

        phrases_unique = list(dict.fromkeys(phrases_pg))
        stats["wk_requested_unique_phrases"] = len(phrases_unique)
        stats["wk_duplicates_before_wk"] = len(phrases_pg) - len(phrases_unique)

        status_log.append(
            f"🔎 WK requested: phrase+group={stats['wk_requested_phrase_group']}, "
            f"unique_phrases={stats['wk_requested_unique_phrases']}, dup={stats['wk_duplicates_before_wk']} | "
            f"region={region_id}"
        )

        wk = WordKeeperService()
        wk_project_name = (
            f"KV API | {target_domain} | geo={region_id} | n={len(phrases_unique)}"
        )

        wk_resp = await wk.check_volume(
            phrases_unique,
            region_id,
            keep_project=wk_keep_project,
            with_meta=True,
            project_name=wk_project_name,
        )

        volume_map = wk_resp.get("results", {})
        meta = wk_resp.get("meta", {})

        stats["wk_returned"] = int(meta.get("returned", len(volume_map)) or 0)
        stats["wk_project_id"] = meta.get("project_id")
        stats["wk_project_name"] = meta.get("project_name")

        missing = [p for p in phrases_unique if p not in volume_map]
        stats["wk_missing"] = len(missing)

        status_log.append(
            f"✅ WK returned: {stats['wk_returned']} | missing: {stats['wk_missing']} | "
            f"project_id={stats['wk_project_id']} | keep={wk_keep_project}"
        )

        # --- FILTER ---
        clean_list = []
        low_freq_count = 0

        for item in unique_data:
            phrase = item["phrase"]
            freq = int(volume_map.get(phrase, 0) or 0)

            if freq >= min_freq:
                item["exact_freq"] = freq
                clean_list.append(item)
            else:
                low_freq_count += 1

        stats["passed_filter_phrase_group"] = len(clean_list)
        status_log.append(
            f"✂️ WK filter (>= {min_freq}): removed {low_freq_count} | passed {len(clean_list)}"
        )

        if not clean_list:
            raise HTTPException(
                400, f"❌ После фильтра WK ничего не осталось (min_freq={min_freq})."
            )

        # --- Dedup for Topvisor ---
        clean_for_tv, dup_phrase = dedup_by_phrase(clean_list)
        stats["dup_phrase_in_passed_filter"] = dup_phrase
        stats["topvisor_sent"] = len(clean_for_tv)

        status_log.append(
            f"🔁 Dedup by phrase (Topvisor-like): dup={dup_phrase} | to import={len(clean_for_tv)}"
        )

        # --- TOPVISOR ---
# --- TOPVISOR ---
        tv = TopvisorService()
        project_id = tv.create_project(target_domain)
        status_log.append(f"✅ Topvisor project created: ID={project_id}")

        # ВАЖНО: добавляем Яндекс + регион + phone и получаем regions_index
        region_index = tv.setup_yandex_mobile_region_and_get_index(project_id, region_id, depth=1)
        status_log.append(f"🔧 Topvisor positions setup: yandex region={region_id} device=phone | region_index={region_index}")

        added = tv.add_keywords(project_id, clean_for_tv)
        stats["topvisor_added"] = int(added or 0)
        status_log.append(f"🚀 Topvisor added: {stats['topvisor_added']}")

        # Запуск проверки ПОЗИЦИЙ один раз
        check_resp = tv.check_positions_once(project_id, region_index=region_index, do_snapshots=0)
        status_log.append(f"📈 Topvisor checker/go response: {check_resp}")

        # Дебаг: посмотрим процент (не обязателен, но полезен)
        percent = tv.get_positions_percent(project_id)
        status_log.append(f"📊 Topvisor positions_percent: {percent}")

        # (если хочешь — можешь сохранить ответ в stats)
        stats["topvisor_positions_check"] = check_resp

        samples = {
            "wk_missing_sample": missing[:30],
            "passed_filter_sample": sorted(
                clean_for_tv, key=lambda x: x.get("exact_freq", 0), reverse=True
            )[:30],
        }

        return {
            "status": "success",
            "project_id": project_id,
            "project_link": f"https://topvisor.com/projects/{project_id}/",
            "final_count": len(clean_for_tv),
            "stats": stats,
            "log": status_log,
            "samples": samples,
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"CRITICAL SYSTEM ERROR: {e}")
        return {"status": "error", "details": str(e), "stats": stats, "log": status_log}
