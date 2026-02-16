import pandas as pd
from io import BytesIO
from typing import Optional


def parse_excel_file(file_content: bytes, filename: Optional[str] = None) -> list:
    """
    Читает Excel (и при желании CSV/TSV по расширению имени файла).
    Ожидает: 1-я колонка = Ключ, 2-я колонка = Группа (URL или категория).
    Возвращает список словарей: [{'phrase': 'купить окно', 'group': '/catalog/windows'}, ...]
    """

    try:
        # Если передали имя файла и это не Excel — попробуем CSV/TSV (на будущее, чтобы не падать)
        name = (filename or "").lower().strip()

        if name.endswith(".csv"):
            df = pd.read_csv(BytesIO(file_content), header=0)
        elif name.endswith(".tsv"):
            df = pd.read_csv(BytesIO(file_content), header=0, sep="\t")
        else:
            # Excel по умолчанию
            df = pd.read_excel(BytesIO(file_content), header=0)

        if df.empty or df.shape[1] < 2:
            print("Ошибка: в файле меньше 2 колонок")
            return []

        # Первые две колонки
        col_keyword = df.columns[0]
        col_group = df.columns[1]

        # Убираем строки, где пустой ключ
        df = df.dropna(subset=[col_keyword])

        results = []
        for _, row in df.iterrows():
            phrase = str(row[col_keyword]).strip()

            raw_group = str(row[col_group]) if pd.notna(row[col_group]) else "General"
            group_name = raw_group.strip() if raw_group else "General"

            if phrase:
                results.append({"phrase": phrase, "group": group_name})

        # Дедуп (ключ+группа)
        unique_results = list(
            {(item["phrase"], item["group"]): item for item in results}.values()
        )

        return unique_results

    except Exception as e:
        print(f"Ошибка чтения Excel: {e}")
        return []
