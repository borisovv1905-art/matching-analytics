"""
Офлайн-пайплайн частотности тем (initial_topic) для вкладки «Классы/Предметы» в app.py.

Запуск (из корня репозитория):
    python topic_frequency_pipeline.py jul aug      # конкретные месяцы
    python topic_frequency_pipeline.py              # все месяцы, для которых есть xlsx

Читает data/prod_{месяц}2026*.xlsx (май из двух файлов тоже подхватится) и пишет в
data/topic_freq/ четыре файла на месяц:
    topic_frequency_{m}.csv             — основные темы (нормализованные, дубли объединены)
    topic_frequency_kr_{m}.csv          — контрольные работы
    topic_frequency_links_{m}.csv       — вместо темы вставлена ссылка (оригинальный текст)
    topic_frequency_other_lang_{m}.csv  — тема без кириллицы (английский и т.п.)
Колонки во всех файлах: dialog_grade, canonical_topic, frequency.

Объединение дублей: внутри каждого класса похожие формулировки кластеризуются
по косинусной близости. Если установлен sentence-transformers — по эмбеддингам
(модель EMBEDDING_MODEL), иначе по TF-IDF символьных n-грамм (scikit-learn).
Название кластера — самая частая формулировка в нём (при равенстве — самая короткая).

Зависимости (только для этого скрипта, приложению они не нужны):
    pip install pandas openpyxl scikit-learn   [+ sentence-transformers]
"""
import argparse
import glob
import html
import os
import re
import sys

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
OUT_DIR = os.path.join(DATA_DIR, 'topic_freq')
ALL_MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

GRADES = range(5, 12)                # как в старых файлах: только 5–11 класс
EMBEDDING_MODEL = 'cointegrated/rubert-tiny2'
EMBED_THRESHOLD = 0.88               # мин. косинусная близость для объединения (эмбеддинги)
TFIDF_THRESHOLD = 0.88               # то же для TF-IDF фолбэка (ниже уже склеивает
                                     # 'деление'/'умножение', 'прямая'/'обратная')
SKIP_SHEETS = {'Итоги_авто', 'ОБЩАЯ_СТАТИСТИКА', 'Статистика', 'Скелет'}
OUT_COLS = ['dialog_grade', 'canonical_topic', 'frequency']

_LINK_RE = re.compile(r'https?://|www\.', re.I)
_TAG_RE = re.compile(r'<[^>]+>')
_KR_RE = re.compile(r'контрольн\w*\s+работ\w*', re.I)

# Служебные обороты, которые не несут смысла темы — вырезаются (порядок важен:
# сначала длинные). Совпадают с тем, что было вычищено в файлах mar–jun.
_SERVICE_PATTERNS = [
    r'анализ контрольной работы\.?',
    r'материал для расширения и углубления знаний',
    r'обобщение и систематизация знаний',
    r'повторение и систематизация знаний',
    r'систематизация знаний',
    r'обобщение и повторение',
    r'повторение и обобщение',
    r'повторение,? обобщение',
    r'итоговое повторение',
    r'повторение',
    r'закрепление по теме',
    r'закрепление',
    r'практическая работа( по теме)?:?',
    r'самостоятельная работа',
    r'дистанционный урок',
    r'решение задач по теме',
    r'решение задач',
    r'задачи на$',
    r'по теме:?',
    r'тема:',
    r'обобщение знаний',
    r'№\s*\d+',
]
_SERVICE_RE = re.compile(r'(?<!\w)(?:' + '|'.join(_SERVICE_PATTERNS) + r')(?!\w)', re.I)


def strip_html(text):
    text = re.sub(r'<br\s*/?>', ' ', str(text), flags=re.I)
    text = _TAG_RE.sub(' ', text)
    return html.unescape(text)


def squash(text):
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' .,;:-–—/|«»"\'()')


def clean_topic(raw):
    """Нормализованная тема или '' если после чистки ничего не осталось"""
    t = strip_html(raw).lower().replace('\xa0', ' ')
    t = re.sub(r'^\s*[\d.]+\s+', '', t)          # нумерация вида '5.6 ', '4. '
    t = re.sub(r'^\s*п\.\s*\d+\.?\s*', '', t)      # 'п.1 ...'
    t = re.sub(r'^\s*[a-zа-я]{2,4}-[\d-]+\.?\s*', '', t)  # коды уроков 'иот-076-2025. ...'
    t = _SERVICE_RE.sub(' ', t)
    t = re.sub(r'[«»"“”]', ' ', t)
    t = re.sub(r'\(\s*\)', ' ', t)
    t = re.sub(r'\s+([.,;:])', r'\1', t)
    t = re.sub(r'([.,;:])\1+', r'\1', t)
    t = squash(t)
    # остатки вида '. .' в начале/между частями
    t = re.sub(r'^(?:[.,;:]\s*)+', '', t)
    t = squash(t)
    return t if len(t) >= 3 else ''


def clean_kr(raw):
    """'Контрольная работа № 6 по теме "Десятичные дроби"' -> 'десятичные дроби'"""
    t = strip_html(raw).lower().replace('\xa0', ' ')
    m = re.search(r'по\s+теме:?\s*(.+)$', t)
    if m:
        t = m.group(1)
    else:
        t = _KR_RE.sub(' ', t)
        t = re.sub(r'№\s*\d+', ' ', t)
    t = re.sub(r'[«»"“”()]', ' ', t)
    t = re.sub(r'\s*/\s*', ' ', t)
    t = re.sub(r'проверочная работа', ' ', t)
    return squash(squash(t)) if len(squash(t)) >= 3 else ''


def has_cyrillic(text):
    return re.search(r'[а-яё]', str(text), re.I) is not None


def load_month(abbr):
    files = sorted(glob.glob(os.path.join(DATA_DIR, f'prod_{abbr}2026*.xlsx')))
    frames = []
    for path in files:
        print(f'  читаю {os.path.basename(path)}')
        sheets = pd.read_excel(path, sheet_name=None, engine='openpyxl',
                               usecols=lambda c: c in ('dialog_id', 'initial_topic', 'dialog_grade'))
        for name, df in sheets.items():
            if name in SKIP_SHEETS or 'initial_topic' not in df.columns or 'dialog_grade' not in df.columns:
                continue
            frames.append(df)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df = df[df['initial_topic'].notna()]
    df['initial_topic'] = df['initial_topic'].astype(str)
    df = df[df['initial_topic'].str.strip() != '']
    df['dialog_grade'] = pd.to_numeric(df['dialog_grade'], errors='coerce')
    return df[df['dialog_grade'].isin(GRADES)].astype({'dialog_grade': int})


_encoder = None


def similarity_matrix(texts):
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _encoder = ('emb', SentenceTransformer(EMBEDDING_MODEL))
            print(f'  объединение дублей: эмбеддинги {EMBEDDING_MODEL}')
        except ImportError:
            from sklearn.feature_extraction.text import TfidfVectorizer
            _encoder = ('tfidf', TfidfVectorizer(analyzer='char_wb', ngram_range=(3, 5), sublinear_tf=True))
            print('  объединение дублей: TF-IDF (sentence-transformers не установлен)')
    kind, enc = _encoder
    if kind == 'emb':
        vecs = enc.encode(texts, normalize_embeddings=True)
        return vecs @ vecs.T, EMBED_THRESHOLD
    vecs = enc.fit_transform(texts)
    return (vecs @ vecs.T).toarray(), TFIDF_THRESHOLD


def merge_duplicates(counts):
    """counts: Series {тема: частота} одного класса -> Series {каноническая тема: частота}"""
    if len(counts) < 2:
        return counts
    texts = counts.index.tolist()
    sim, threshold = similarity_matrix(texts)
    # жадная кластеризация: идём от самых частых тем, каждая забирает
    # ещё не распределённые темы, похожие на неё сильнее порога
    order = sorted(range(len(texts)), key=lambda i: (-counts.iloc[i], len(texts[i])))
    assigned = {}
    for i in order:
        if i in assigned:
            continue
        assigned[i] = i
        for j in np.where(sim[i] >= threshold)[0]:
            if j not in assigned:
                assigned[j] = i
    canon = pd.Series([texts[assigned[i]] for i in range(len(texts))], index=texts)
    return counts.groupby(canon).sum()


def to_frame(df, col, merge):
    rows = []
    for grade, g in df.groupby('dialog_grade'):
        counts = g[col].value_counts()
        if merge:
            counts = merge_duplicates(counts)
        for topic, freq in counts.items():
            rows.append((grade, topic, int(freq)))
    out = pd.DataFrame(rows, columns=OUT_COLS)
    return out.sort_values(['frequency', 'dialog_grade'], ascending=[False, True]).reset_index(drop=True)


def process_month(abbr, out_dir=OUT_DIR):
    print(f'== {abbr}')
    df = load_month(abbr)
    if df is None:
        print('  нет xlsx — пропуск')
        return
    raw = df['initial_topic']
    # через .map, а не .str.contains: на pyarrow-строках (pandas 3) \w не ловит кириллицу
    is_link = raw.map(lambda t: bool(_LINK_RE.search(t)))
    is_other = ~is_link & ~raw.map(has_cyrillic)
    is_kr = ~is_link & ~is_other & raw.map(lambda t: bool(_KR_RE.search(t)))
    is_main = ~is_link & ~is_other & ~is_kr

    links = df[is_link].assign(topic=raw[is_link].str.strip())
    other = df[is_other].assign(topic=raw[is_other].map(lambda t: squash(strip_html(t).lower())))
    kr = df[is_kr].assign(topic=raw[is_kr].map(clean_kr))
    main = df[is_main].assign(topic=raw[is_main].map(clean_topic))
    kr, main, other = kr[kr['topic'] != ''], main[main['topic'] != ''], other[other['topic'] != '']

    os.makedirs(out_dir, exist_ok=True)
    outputs = {
        f'topic_frequency_{abbr}.csv': to_frame(main, 'topic', merge=True),
        f'topic_frequency_kr_{abbr}.csv': to_frame(kr, 'topic', merge=True),
        f'topic_frequency_links_{abbr}.csv': to_frame(links, 'topic', merge=False),
        f'topic_frequency_other_lang_{abbr}.csv': to_frame(other, 'topic', merge=False),
    }
    print(f'  тем заполнено (5–11 кл.): {len(df)}')
    for name, out in outputs.items():
        out.to_csv(os.path.join(out_dir, name), index=False, encoding='utf-8-sig')
        print(f'  {name}: {len(out)} тем, сумма частот {out["frequency"].sum()}')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('months', nargs='*', help='jan feb ... (по умолчанию — все, для которых есть xlsx)')
    parser.add_argument('--out', default=OUT_DIR, help='куда писать csv (по умолчанию data/topic_freq)')
    args = parser.parse_args()
    months = args.months or [m for m in ALL_MONTHS if glob.glob(os.path.join(DATA_DIR, f'prod_{m}2026*.xlsx'))]
    for m in months:
        if m not in ALL_MONTHS:
            sys.exit(f'Неизвестный месяц: {m}. Допустимо: {" ".join(ALL_MONTHS)}')
        process_month(m, args.out)


if __name__ == '__main__':
    main()
