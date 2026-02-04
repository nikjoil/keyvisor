import pandas as pd
from io import BytesIO

def parse_excel_file(file_content: bytes) -> list:
    """
    Читает Excel.
    Ожидает: 1-я колонка = Ключ, 2-я колонка = Группа (URL или категория).
    Возвращает список словарей: [{'phrase': 'купить окно', 'group': '/catalog/windows'}, ...]
    """
    try:
        # Считываем файл
        df = pd.read_excel(BytesIO(file_content), header=0)

        if df.empty or df.shape[1] < 2:
            print("Ошибка: в файле меньше 2 колонок")
            return []

        # Получаем названия первых двух колонок (A и B)
        col_keyword = df.columns[0]
        col_group = df.columns[1] # Это URL из твоего скрина

        # Убираем строки, где пустой ключ
        df = df.dropna(subset=[col_keyword])

        results = []
        for index, row in df.iterrows():
            phrase = str(row[col_keyword]).strip()

            # Если URL пустой, назовем группу "Общая"
            raw_group = str(row[col_group]) if pd.notna(row[col_group]) else "General"
            group_name = raw_group.strip()

            if phrase:
                results.append({
                    "phrase": phrase,
                    "group": group_name
                })

        # Убираем полные дубликаты (один и тот же ключ в одной и той же группе)
        # Для этого превратим список словарей в set кортежей, а потом обратно
        unique_results = list({(item['phrase'], item['group']): item for item in results}.values())

        return unique_results

    except Exception as e:
        print(f"Ошибка чтения Excel: {e}")
        return []
