import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import re
import time
import requests
import gc

# Настройка страницы
st.set_page_config(
    page_title="R&D Аналитика: Мэтчинг",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === КОНФИГУРАЦИЯ: ФАЙЛЫ НА GITHUB ===
# Базовая ссылка на raw-файлы в репозитории. Поменяй под свой репозиторий/ветку.
GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/borisovv1905-art/matching-analytics/main/data'

# Для каждого месяца — список xlsx-файлов (может быть один файл или несколько,
# например май разбит на 2 файла из-за размера — они просто объединятся в один датафрейм).
MONTHS = {
    'Январь': {'files': ['prod_jan2026.xlsx']},
    'Февраль': {'files': ['prod_feb2026.xlsx']},
    'Март': {'files': ['prod_mar2026.xlsx']},
    'Апрель': {'files': ['prod_apr2026.xlsx']},
    'Май': {'files': ['prod_may2026(mesh).xlsx', 'prod_may2026(standalone_eljur_search).xlsx']},
    'Июнь': {'files': ['prod_jun2026.xlsx']},
}

# Хронологический порядок месяцев — единый источник правды для всей сортировки.
# Порядок берётся из MONTHS, поэтому просто держи MONTHS в хронологическом порядке.
MONTH_ORDER = list(MONTHS.keys())
ALL_MONTHS_OPTION = '🗓 Все месяцы'

# Служебные листы внутри xlsx, которые НЕ являются данными диалогов — их пропускаем
SKIP_SHEETS = {'Итоги_авто', 'ОБЩАЯ_СТАТИСТИКА', 'Статистика', 'Скелет'}

# Переименование "родных" колонок из выгрузки в названия, которые ожидает остальной код
COLUMN_RENAME = {
    'Метчинг': 'status_only',
    'Постметчинг': 'status_only_post',
}

# Названия листов внутри xlsx уже содержат месяц (напр. 'eljur_март', 'standalone_январь').
# Это ломает сравнение источников при объединении месяцев — 'eljur' за разные месяцы
# превращается в кучу разных "источников" вместо одного. Чистим суффикс месяца.
_MONTH_SUFFIXES = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
                    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

def extract_base_source(sheet_name):
    """'eljur_март' -> 'eljur'; 'standalone_январь' -> 'standalone'"""
    if not isinstance(sheet_name, str):
        return sheet_name
    parts = sheet_name.rsplit('_', 1)
    if len(parts) == 2 and parts[1].lower() in _MONTH_SUFFIXES:
        return parts[0]
    return sheet_name

# === ФУНКЦИИ ЗАГРУЗКИ ДАННЫХ ===

@st.cache_data(ttl=3600)
def load_xlsx_from_github(filename):
    """Скачивает xlsx с GitHub и читает все листы разом"""
    url = f"{GITHUB_RAW_BASE}/{filename}"
    try:
        from io import BytesIO
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        excel_file = BytesIO(response.content)
        sheets = pd.read_excel(excel_file, sheet_name=None, engine='openpyxl')

        # ⚠️ Служебные колонки-гиганты (схемы/промпты для LLM) — само приложение
        # их нигде не использует, но они весят почти столько же, сколько текст
        # диалога. Дропаем сразу при чтении, чтобы они вообще не попадали в кэш.
        USELESS_HEAVY_COLS = [
            'task_schema', 'topic_schema_main', 'topic_schema_boss',
            'topic_schema_summary', 'topic_schema_plan'
        ]
        for sheet_name, df in sheets.items():
            drop_cols = [c for c in USELESS_HEAVY_COLS if c in df.columns]
            if drop_cols:
                sheets[sheet_name] = df.drop(columns=drop_cols)

        return sheets
    except Exception as e:
        st.error(f"💥 Ошибка загрузки {filename}: {e}")
        return {}

def get_dialog_text(month_name, dialog_id):
    """
    Достаёт полный текст ОДНОГО диалога по требованию, а не для всех разом.
    load_xlsx_from_github уже кэширован — файл с GitHub второй раз не скачивается,
    просто ищем нужную строку в уже загруженных (кэшированных) листах.
    """
    month_config = MONTHS.get(month_name, {})
    filenames = month_config.get('files', [])

    for filename in filenames:
        all_sheets = load_xlsx_from_github(filename)
        for sheet_name, sheet_df in all_sheets.items():
            if sheet_name in SKIP_SHEETS or sheet_df.empty:
                continue
            if 'dialog_id' not in sheet_df.columns or 'Диалог' not in sheet_df.columns:
                continue
            match = sheet_df[sheet_df['dialog_id'] == dialog_id]
            if not match.empty:
                return match.iloc[0]['Диалог']
    return None

@st.cache_data(ttl=3600)
def load_month_data(month_name):
    """Загружает и объединяет данные месяца из xlsx-файлов на GitHub"""
    month_config = MONTHS.get(month_name, {})
    filenames = month_config.get('files', [])
    data = {'chart': pd.DataFrame(), 'stats': pd.DataFrame(),
            'class_stats': pd.DataFrame(), 'skeleton': pd.DataFrame()}

    progress_bar = st.progress(0)
    status_text = st.empty()

    try:
        dialog_frames = []
        n_files = max(len(filenames), 1)

        for i, filename in enumerate(filenames):
            status_text.text(f"📊 {month_name}: загрузка {filename}...")
            all_sheets = load_xlsx_from_github(filename)

            for sheet_name, sheet_df in all_sheets.items():
                if sheet_name in SKIP_SHEETS or sheet_df.empty:
                    continue
                if 'dialog_id' not in sheet_df.columns:
                    continue  # это не лист с диалогами
                sheet_df = sheet_df.copy()
                sheet_df['источник_лист'] = extract_base_source(sheet_name)  # standalone/eljur/mesh/myschool/search (без месяца)
                dialog_frames.append(sheet_df)

            progress_bar.progress(int((i + 1) / n_files * 70))

        if dialog_frames:
            df = pd.concat(dialog_frames, ignore_index=True, sort=False)
            df = df.rename(columns=COLUMN_RENAME)
            data['chart'] = df

        progress_bar.progress(85)

        # 🆕 class_stats считаем на лету (раньше был отдельный лист "Статистика")
        status_text.text(f"📚 {month_name}: считаем статистику по классам...")
        if not data['chart'].empty and 'Класс' in data['chart'].columns and 'Предмет' in data['chart'].columns:
            data['class_stats'] = (
                data['chart']
                .groupby(['Класс', 'Предмет'])
                .size()
                .reset_index(name='Количество учеников')
            )

        # 🦴 skeleton берём прямо из общего датафрейма (там уже есть колонка "Скелет")
        # ⚠️ 'Диалог' (полный текст) сюда НЕ включаем — держать текст всех диалогов
        # разом в памяти дорого. Полный текст подгружается по требованию через
        # get_dialog_text() только для того диалога, который реально открыли.
        if not data['chart'].empty and 'Скелет' in data['chart'].columns:
            skel_cols = [c for c in ['dialog_id', 'Скелет', 'dialog_grade', 'dialog_role'] if c in data['chart'].columns]
            data['skeleton'] = data['chart'][skel_cols].copy()

            # ⚠️ ВАЖНО ДЛЯ ПАМЯТИ: 'Диалог' и 'Скелет' — самые тяжёлые текстовые поля
            # (полный текст диалога на десятки тысяч строк). Они уже сохранены выше
            # в data['skeleton'] — держать их ЕЩЁ РАЗ в data['chart'] означает
            # удвоение памяти на ровном месте. Ни один другой график/вкладка их
            # из chart не читает (только из data['skeleton']), поэтому дропаем.
            #
            # 🆕 Плюс отдельно нашлись служебные колонки-гиганты (task_schema,
            # topic_schema_*) — это схемы/промпты для LLM, само приложение их
            # НИГДЕ не использует, но они весят почти столько же, сколько 'Диалог'.
            # Дропаем и их — на 25 тыс. строк это реально экономит сотни МБ.
            heavy_cols = [c for c in [
                'Диалог', 'Скелет',
                'task_schema', 'topic_schema_main', 'topic_schema_boss',
                'topic_schema_summary', 'topic_schema_plan'
            ] if c in data['chart'].columns]
            if heavy_cols:
                data['chart'] = data['chart'].drop(columns=heavy_cols)

        progress_bar.progress(100)
    finally:
        progress_bar.empty()
        status_text.empty()

    return data

# === 🆕 УЛУЧШЕННАЯ ВИЗУАЛИЗАЦИЯ СКЕЛЕТА ===

def visualize_skeleton_enhanced(skeleton_text, dialog_text=None, dialog_id=None):
    """Интерактивная ступенчатая визуализация с деталями"""
    
    if pd.isna(skeleton_text) or skeleton_text == '':
        return "❌ Нет данных о скелете"
    
    blocks = [b.strip() for b in str(skeleton_text).split('\n') if b.strip()]
    if not blocks:
        return "❌ Пустой скелет"
    
    x_vals, y_vals, labels, colors, sizes, hover_texts, block_types = [], [], [], [], [], [], []
    depth = 0
    step_num = 0
    
    color_map = {
        'plan': '#2E86AB',
        'info': '#A23B72',
        'action': '#F18F01',
        'problem': '#C73E1D',
        'bossaction': '#6A994E',
        'summary': '#386641',
        'other': '#6C757D'
    }
    
    for i, block in enumerate(blocks):
        block_type = 'other'
        if block == 'plan':
            label, block_type = '📋 План', 'plan'
            depth = 0
        elif block.startswith('info'):
            label, block_type = '📚 Теория', 'info'
            match = re.search(r'\d+', block)
            step_num = int(match.group()) if match else step_num
            depth = 0
        elif block.startswith('action'):
            label, block_type = '❓ Вопрос', 'action'
            depth += 1
        elif block == 'problem':
            label, block_type = '🎯 Финал', 'problem'
            depth = 0
        elif block.startswith('bossaction'):
            label, block_type = '🔧 Подсказка', 'bossaction'
            depth += 1
        elif block == 'summary':
            label, block_type = '✅ Итог', 'summary'
            depth = 0
        else:
            label = block
        
        x_vals.append(i)
        y_vals.append(-depth)
        colors.append(color_map.get(block_type, '#6C757D'))
        block_types.append(block_type)
        
        sizes.append(15)
        
        hover_text = f"<b style='font-size:14px'>{label}</b><br>"
        hover_text += f"Порядок: {i+1}<br>"
        hover_text += f"Глубина: {depth}<br>"
        if block_type == 'action':
            hover_text += f"Шаг: {step_num}"
        hover_texts.append(hover_text)
        
        labels.append(f"{i+1}. {label}")
    
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=x_vals, y=y_vals,
        mode='lines+markers+text',
        line=dict(color='#2E86AB', width=4),
        marker=dict(
            size=sizes,
            color=colors,
            line=dict(width=2, color='white'),
            opacity=1.0
        ),
        text=labels,
        textposition='top center',
        textfont=dict(size=12, color='#333333', family='Arial Black'),
        hovertext=hover_texts,
        hoverinfo='text',
        name='Скелет',
        hoverlabel=dict(bgcolor='white', font_size=14, font_family='Arial')
    ))
    
    for i in range(len(blocks)-1):
        if blocks[i].startswith('action') and not blocks[i+1].startswith('action'):
            fig.add_trace(go.Scatter(
                x=[i, i], y=[y_vals[i], 0],
                mode='lines',
                line=dict(color='#FF6B6B', width=2, dash='dash'),
                showlegend=False, hoverinfo='skip', name='Переход'
            ))
    
    if len(blocks) > 0:
        last_type = block_types[-1]
        if last_type in ['action', 'bossaction']:
            fig.add_trace(go.Scatter(
                x=[len(blocks)-1], y=[y_vals[-1]],
                mode='markers',
                marker=dict(size=20, color='red', symbol='x', line=dict(width=3, color='darkred')),
                name='🔴 Отвал здесь', hoverinfo='skip', showlegend=True
            ))
    
    fig.update_layout(
        title=dict(
            text=f"🦴 Скелет диалога{' #' + dialog_id[:8] if dialog_id else ''}",
            font=dict(size=20, family='Arial Black', color='#333')
        ),
        xaxis_title=dict(
            text="Порядок блоков",
            font=dict(size=14, family='Arial', color='#555')
        ),
        yaxis_title=dict(
            text="Глубина погружения",
            font=dict(size=14, family='Arial', color='#555')
        ),
        height=500,
        hovermode='closest',
        yaxis=dict(
            autorange='reversed', 
            title='Шаг ↓', 
            showgrid=True, 
            gridcolor='rgba(0,0,0,0.15)',
            zerolinecolor='#333',
            zerolinewidth=2
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(0,0,0,0.05)'
        ),
        plot_bgcolor='white',
        paper_bgcolor='white',
        legend=dict(
            orientation='h', 
            yanchor='bottom', 
            y=1.02, 
            xanchor='right', 
            x=1,
            font=dict(size=12)
        ),
        margin=dict(l=60, r=60, t=80, b=60)
    )
    
    return fig, blocks, block_types

def load_multiple_months(month_list):
    """Загружает данные для нескольких месяцев для сравнения"""
    combined = {}
    for month in month_list:
        data = load_month_data(month)
        if 'chart' in data and not data['chart'].empty:
            data['chart']['Месяц'] = month
            combined[month] = data['chart']
    return pd.concat(combined.values(), ignore_index=True) if combined else pd.DataFrame()

def main():
    st.title("📊 R&D Аналитика: Мэтчинг")
    st.markdown("Визуализация данных по диалогам за январь–июнь 2026")
    
    with st.sidebar:
        st.header("🎛 Фильтры")

        # ВАЖНО: index=1 (конкретный месяц), а НЕ 0 ("Все месяцы") — иначе при
        # каждом холодном запуске приложение тянет все месяцы разом, включая
        # самый тяжёлый (mesh), и может падать по памяти на Streamlit Cloud.
        month_options = [ALL_MONTHS_OPTION] + MONTH_ORDER
        selected_month = st.selectbox("📅 Месяц", month_options, index=1)

        with st.spinner('Загрузка данных...'):
            if selected_month == ALL_MONTHS_OPTION:
                # ⚠️ Самый тяжёлый режим по памяти — здесь легко упереться в лимит
                # Streamlit Cloud (обычно ~1GB), поэтому текстовые поля не дублируем:
                # 'Диалог'/'Скелет' держим ТОЛЬКО в skeleton-датафрейме, а не ещё раз в chart.
                HEAVY_TEXT_COLS = ['Диалог', 'Скелет']

                chart_frames, skel_frames = [], []
                for m in MONTH_ORDER:
                    d = load_month_data(m)
                    if not d['chart'].empty:
                        c = d['chart'].copy()
                        c['Месяц'] = m
                        # тяжёлый текст убираем из "основного" датафрейма — он уже есть в skeleton
                        drop_cols = [col for col in HEAVY_TEXT_COLS if col in c.columns]
                        if drop_cols:
                            c = c.drop(columns=drop_cols)
                        chart_frames.append(c)
                    sk = d.get('skeleton', pd.DataFrame())
                    if not sk.empty:
                        s = sk.copy()
                        s['Месяц'] = m
                        skel_frames.append(s)

                chart_all = pd.concat(chart_frames, ignore_index=True) if chart_frames else pd.DataFrame()
                skel_all = pd.concat(skel_frames, ignore_index=True) if skel_frames else pd.DataFrame()

                # освобождаем промежуточные списки сразу, не дожидаясь сборщика мусора
                del chart_frames, skel_frames
                gc.collect()

                st.sidebar.caption(
                    "⚠️ Режим «Все месяцы» загружает много данных сразу — "
                    "может занять больше времени, чем один месяц."
                )

                class_all = pd.DataFrame()
                if not chart_all.empty and 'Класс' in chart_all.columns and 'Предмет' in chart_all.columns:
                    class_all = (
                        chart_all.groupby(['Класс', 'Предмет'])
                        .size()
                        .reset_index(name='Количество учеников')
                    )

                data = {
                    'chart': chart_all,
                    'stats': pd.DataFrame(),
                    'class_stats': class_all,
                    'skeleton': skel_all,
                }
            else:
                data = load_month_data(selected_month)

        selected_grade = 'Все'
        if 'chart' in data and not data['chart'].empty and 'dialog_grade' in data['chart'].columns:
            grades = ['Все'] + sorted(data['chart']['dialog_grade'].dropna().unique().astype(str).tolist())
            selected_grade = st.selectbox("📚 Класс", grades)

        selected_role = 'Все'
        if 'chart' in data and not data['chart'].empty and 'dialog_role' in data['chart'].columns:
            roles = ['Все'] + sorted(data['chart']['dialog_role'].dropna().unique().tolist())
            selected_role = st.selectbox("👤 Роль", roles)

        selected_product = 'Все'
        if 'chart' in data and not data['chart'].empty and 'product_slug' in data['chart'].columns:
            products = ['Все'] + sorted(data['chart']['product_slug'].dropna().unique().tolist())
            selected_product = st.selectbox("📦 Продукт", products)

        with st.expander("🔧 Отладка", expanded=False):
            if st.checkbox("Показать продукты", value=False):
                if 'product_slug' in data['chart'].columns:
                    st.write("Доступные:", data['chart']['product_slug'].dropna().unique())

        selected_source = 'Все'
        if 'chart' in data and not data['chart'].empty and 'источник_лист' in data['chart'].columns:
            sources = ['Все'] + sorted(data['chart']['источник_лист'].dropna().unique().tolist())
            if len(sources) > 2:
                selected_source = st.selectbox("🗂 Источник", sources)

        # === Фильтр по источнику входа: журнал (тема заполнена) / баннер (тема пустая) ===
        # initial_topic заполнено только там, где ученик пришёл из журнала (там тема
        # урока уже известна заранее); если пусто — значит зашёл с баннера/без темы.
        entry_source_filter = 'Все'
        has_initial_topic_col = (
            'chart' in data and not data['chart'].empty
            and 'initial_topic' in data['chart'].columns
        )
        if has_initial_topic_col:
            entry_source_filter = st.selectbox(
                "🚪 Источник входа",
                ['Все', '📓 Из журнала (тема есть)', '🖼 С баннера (темы нет)']
            )

        date_range = None
        if 'chart' in data and not data['chart'].empty and 'activity_dt' in data['chart'].columns:
            data['chart']['activity_dt'] = pd.to_datetime(data['chart']['activity_dt'], errors='coerce')
            min_date = data['chart']['activity_dt'].min()
            max_date = data['chart']['activity_dt'].max()
            if pd.notna(min_date) and pd.notna(max_date):
                date_range = st.date_input(
                    "📆 Период",
                    value=(min_date.date(), max_date.date()),
                    min_value=min_date.date(),
                    max_value=max_date.date()
                )

        st.divider()

        if st.button("🔄 Обновить данные", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if 'chart' not in data or data['chart'].empty:
        st.warning("⚠️ Не удалось загрузить данные.")
        return
    
    if 'activity_dt' in data['chart'].columns:
        
        min_date = data['chart']['activity_dt'].min()
        max_date = data['chart']['activity_dt'].max()
        
        if pd.notna(min_date) and pd.notna(max_date):
            st.sidebar.info(
                f"📅 **Диапазон дат в файле:**\n"
                f"{min_date.date()} — {max_date.date()}\n\n"
                f"Выбран месяц: **{selected_month}**"
            )

    df = data['chart'].copy()
    filter_mask = pd.Series([True] * len(df), index=df.index)

    if selected_grade != 'Все' and 'dialog_grade' in df.columns:
        filter_mask &= df['dialog_grade'].astype(str) == selected_grade

    if selected_role != 'Все' and 'dialog_role' in df.columns:
        filter_mask &= df['dialog_role'] == selected_role
    
    if selected_product != 'Все' and 'product_slug' in df.columns:
        filter_mask &= df['product_slug'] == selected_product

    if selected_source != 'Все' and 'источник_лист' in df.columns:
        filter_mask &= df['источник_лист'] == selected_source

    if entry_source_filter != 'Все' and 'initial_topic' in df.columns:
        if entry_source_filter == '📓 Из журнала (тема есть)':
            filter_mask &= df['initial_topic'].notna() & (df['initial_topic'].astype(str).str.strip() != '')
        else:  # 🖼 С баннера (темы нет)
            filter_mask &= df['initial_topic'].isna() | (df['initial_topic'].astype(str).str.strip() == '')

    if date_range and 'activity_dt' in df.columns:
        filter_mask &= (df['activity_dt'].dt.date >= date_range[0]) & \
                    (df['activity_dt'].dt.date <= date_range[1])

    df = df[filter_mask].copy()

    if 'skeleton' in data and not data['skeleton'].empty and 'dialog_id' in data['skeleton'].columns:
        # ⚠️ Раньше тут была проверка "если после фильтров осталось 0 диалогов — не фильтровать",
        # из-за которой скелеты при 0 совпадениях показывали ВСЕ диалоги вместо пустого списка.
        # Теперь фильтруем всегда, даже если результат — пустой набор id.
        filtered_ids = df['dialog_id'].unique() if 'dialog_id' in df.columns else []
        data['skeleton'] = data['skeleton'][data['skeleton']['dialog_id'].isin(filtered_ids)].copy()

    if len(df) < len(data['chart']):
        filters_applied = []
        if selected_grade != 'Все':
            filters_applied.append(f"класс {selected_grade}")
        if selected_role != 'Все':
            filters_applied.append(f"роль: {selected_role}")
        if date_range:
            filters_applied.append(f"период: {date_range[0]} - {date_range[1]}")
        if selected_product != 'Все':
            filters_applied.append(f"продукт: {selected_product}")
        if selected_source != 'Все':
            filters_applied.append(f"источник: {selected_source}")
        if entry_source_filter != 'Все':
            filters_applied.append(f"вход: {entry_source_filter}")
        
        st.info(f"🔍 Показано {len(df):,} из {len(data['chart']):,} диалогов (фильтры: {', '.join(filters_applied)})")
    
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📊 Воронки", "📈 Динамика", "🦴 Скелеты", 
        "📚 Классы/Предметы", "⏱️ Время", "🔄 Сравнение месяцев", "🔀 Сравнение источников"
    ])
    
    with tab1:
        st.subheader("🔄 Воронка мэтчинга")
        
        if len(df) == 0:
            st.warning("⚠️ Нет данных для отображения воронки с текущими фильтрами.")
        elif 'status_only' not in df.columns:
            st.error("❌ В данных не найден столбец 'status_only'.")
        else:
            total_count = len(df)
            
            funnel_stages = [
                {
                    'Этап': '📥 Всего диалогов',
                    'count': total_count,
                    'drop': 0
                }
            ]
            
            empty_requests = len(df[df['status_only'] == '(-) ничего не ввели'])
            after_empty = total_count - empty_requests
            funnel_stages.append({
                'Этап': '✅ Без пустых запросов',
                'count': after_empty,
                'drop': -empty_requests
            })
            
            task_requests = len(df[df['status_only'] == '(-) ввели задачу'])
            after_task = after_empty - task_requests
            funnel_stages.append({
                'Этап': '✅ Без задач вместо темы',
                'count': after_task,
                'drop': -task_requests
            })
            
            not_math = len(df[df['status_only'] == '(-) ввели тему не по математике'])
            after_math = after_task - not_math
            funnel_stages.append({
                'Этап': '✅ По математике',
                'count': after_math,
                'drop': -not_math
            })
            
            not_grade = len(df[df['status_only'] == '(-) ввели мат тему не 5-11'])
            after_grade = after_math - not_grade
            funnel_stages.append({
                'Этап': '✅ 5-11 класс',
                'count': after_grade,
                'drop': -not_grade
            })
            
            left_after = len(df[df['status_only'] == '(-) ушли после мэтчинга'])
            after_left = after_grade - left_after
            funnel_stages.append({
                'Этап': '✅ Не ушли сразу',
                'count': after_left,
                'drop': -left_after
            })
            
            success = len(df[df['status_only'] == '(-) всё хорошо'])
            matching_error = after_left - success
            
            funnel_stages.append({
                'Этап': '🎉 Успешный мэтчинг',
                'count': success,
                'drop': -matching_error if matching_error > 0 else 0
            })
            
            funnel_df = pd.DataFrame(funnel_stages)
            
            funnel_df['Текст'] = funnel_df.apply(
                lambda row: f"{row['count']:,} {row['drop']:+,}" if row['drop'] != 0 else f"{row['count']:,}",
                axis=1
            )
            
            filter_info = f" (после фильтров: {total_count:,} диалогов)" if total_count < len(data['chart']) else ""
            
            fig_funnel = px.funnel(
                funnel_df,
                x='count',
                y='Этап',
                title=f"Воронка мэтчинга — {selected_month}{filter_info}",
                color='Этап',
                color_discrete_sequence=px.colors.qualitative.Set2
            )
            
            for i, row in funnel_df.iterrows():
                if row['drop'] != 0:
                    fig_funnel.add_annotation(
                        x=row['count'],
                        y=row['Этап'],
                        text=f"({row['drop']:+,})",
                        showarrow=False,
                        font=dict(size=10, color='red'),
                        xshift=50,
                        yshift=0
                    )
            
            fig_funnel.update_layout(
                height=600,
                showlegend=False,
                yaxis=dict(autorange='reversed')
            )
            
            st.plotly_chart(fig_funnel, use_container_width=True)
            
            st.divider()
            st.subheader("🎯 Пост-мэтчинг: углубление в диалог")
            
            if 'status_only_post' in df.columns:
                successful_df = df[df['status_only'] == '(-) всё хорошо']
                
                if len(successful_df) > 0:
                    total_success = len(successful_df)
                    
                    post_funnel = [
                        {
                            'Этап': '🎉 Успешный мэтчинг',
                            'count': total_success,
                            'drop': 0
                        }
                    ]
                    
                    saw_plan = len(successful_df[successful_df['status_only_post'] == '(-) ушли после плана'])
                    after_plan = total_success - saw_plan
                    post_funnel.append({
                        'Этап': '📋 Увидели план',
                        'count': after_plan,
                        'drop': -saw_plan
                    })
                    
                    left_step1 = len(successful_df[successful_df['status_only_post'] == '(-) ушли после первого шага'])
                    after_step1 = after_plan - left_step1
                    post_funnel.append({
                        'Этап': '🚶 Прошли шаг 1',
                        'count': after_step1,
                        'drop': -left_step1
                    })
                    
                    left_step2 = len(successful_df[successful_df['status_only_post'] == '(-) ушли после 2-го и более шага'])
                    after_step2 = after_step1 - left_step2
                    post_funnel.append({
                        'Этап': '🚶🚶 Прошли 2+ шага',
                        'count': after_step2,
                        'drop': -left_step2
                    })
                    
                    saw_final = len(successful_df[successful_df['status_only_post'] == '(-) увидели финальную задачу'])
                    after_final = after_step2 - saw_final
                    post_funnel.append({
                        'Этап': '🎯 Увидели финал',
                        'count': after_final,
                        'drop': -saw_final
                    })
                    
                    solved_final = len(successful_df[successful_df['status_only_post'] == '(-) решили финальную задачу'])
                    post_funnel.append({
                        'Этап': '🏆 Решили финал',
                        'count': solved_final,
                        'drop': -(after_final - solved_final) if after_final > solved_final else 0
                    })
                    
                    post_df = pd.DataFrame(post_funnel)
                    post_df['Текст'] = post_df.apply(
                        lambda row: f"{row['count']:,} {row['drop']:+,}" if row['drop'] != 0 else f"{row['count']:,}",
                        axis=1
                    )
                    
                    fig_post = px.funnel(
                        post_df,
                        x='count',
                        y='Этап',
                        title="Воронка пост-мэтчинга (последовательная)",
                        color='Этап',
                        color_discrete_sequence=px.colors.sequential.Blues
                    )
                    
                    for i, row in post_df.iterrows():
                        if row['drop'] != 0:
                            fig_post.add_annotation(
                                x=row['count'],
                                y=row['Этап'],
                                text=f"({row['drop']:+,})",
                                showarrow=False,
                                font=dict(size=10, color='red'),
                                xshift=50
                            )
                    
                    fig_post.update_layout(
                        height=500,
                        showlegend=False,
                        yaxis=dict(autorange='reversed')
                    )
                    
                    st.plotly_chart(fig_post, use_container_width=True)
                    
                    st.write(f"**Всего успешных:** {total_success:,}")
                    st.write(f"**Решили финал:** {solved_final:,} ({solved_final/total_success*100:.1f}%)")
                else:
                    st.info("ℹ️ Нет успешных диалогов для анализа пост-мэтчинга.")
            else:
                st.warning("⚠️ Столбец 'status_only_post' не найден в данных.")
    
    with tab2:
        st.subheader("📈 Количество диалогов по датам")
        if 'activity_dt' in df.columns:
            df['date'] = df['activity_dt'].dt.date
            daily_counts = df.groupby('date').size().reset_index(name='Всего')
            if 'status_only' in df.columns:
                success_by_date = df[df['status_only'] == '(-) всё хорошо']\
                    .groupby(df['activity_dt'].dt.date).size()
                daily_counts = daily_counts.set_index('date')
                daily_counts['Успешные'] = success_by_date
                daily_counts = daily_counts.reset_index()
                daily_counts['Успешные'] = daily_counts['Успешные'].fillna(0).astype(int)
            
            fig_line = px.line(daily_counts, x='date', y=['Всего'] + (['Успешные'] if 'Успешные' in daily_counts.columns else []),
                              title=f"Динамика диалогов — {selected_month}", labels={'value': 'Количество', 'date': 'Дата'})
            fig_line.update_layout(height=400, hovermode='x unified')
            st.plotly_chart(fig_line, use_container_width=True)
            
            col1, col2, col3 = st.columns(3)
            with col1: st.metric("📥 Всего диалогов", f"{len(df):,}")
            with col2:
                if 'status_only' in df.columns:
                    success = len(df[df['status_only'] == '(-) всё хорошо'])
                    st.metric("✅ Успешный мэтчинг", f"{success:,}", delta=f"{success/len(df)*100:.1f}%")
            with col3:
                if 'Время сессии в секундах' in df.columns:
                    st.metric("⏱️ Ср. время", f"{df['Время сессии в секундах'].mean()/60:.1f} мин")
        else:
            st.info("ℹ️ Столбец activity_dt не найден")
    
    with tab3:
        st.subheader("🦴 Визуализация скелетов диалогов")
        
        if 'skeleton' in data and not data['skeleton'].empty:
            skel_df = data['skeleton']
            
            search_id = ""
            if 'selected_dialog_id' in st.session_state:
                search_id = st.session_state['selected_dialog_id']
                del st.session_state['selected_dialog_id']
                st.info(f"🎯 Открыт диалог из вкладки «Время»: {search_id[:36]}...")
            
            if not search_id:
                search_id = st.text_input("🔍 ID диалога (или часть)", placeholder="003a9e82...")
            
            st.divider()
            st.subheader("📋 Быстрый выбор диалога")
            
            if len(skel_df) > 0 and 'dialog_id' in skel_df.columns:
                dialog_options = {}
                for idx, row in skel_df.head(100).iterrows():
                    dialog_id = row['dialog_id']
                    dialog_info = f"{dialog_id[:36]}..."
                    
                    if 'Тег' in row and pd.notna(row['Тег']):
                        dialog_info += f" | {row['Тег']}"
                    if 'dialog_grade' in row and pd.notna(row['dialog_grade']):
                        dialog_info += f" | {row['dialog_grade']} кл."
                    if 'dialog_role' in row and pd.notna(row['dialog_role']):
                        dialog_info += f" | {row['dialog_role']}"
                    
                    dialog_options[dialog_info] = dialog_id
                
                selected_dialog_label = st.selectbox(
                    "Выбери диалог из списка",
                    options=list(dialog_options.keys()),
                    index=None,
                    placeholder="Нажми для выбора..."
                )
                
                if selected_dialog_label:
                    search_id = dialog_options[selected_dialog_label]
                    st.info(f"✅ Выбран диалог: {search_id[:36]}...")
            
            if search_id and 'dialog_id' in skel_df.columns:
                result = skel_df[skel_df['dialog_id'].astype(str).str.contains(search_id, case=False)]
                if not result.empty:
                    row = result.iloc[0]
                    skeleton = row.get('Скелет', row.get('скелет', ''))

                    # 🆕 Полный текст диалога подгружаем ТОЛЬКО для этого одного диалога,
                    # а не держим его в памяти для всех сразу
                    lookup_month = row.get('Месяц', selected_month)
                    with st.spinner('Загрузка текста диалога...'):
                        dialog = get_dialog_text(lookup_month, row['dialog_id']) or ''
                    
                    result_viz = visualize_skeleton_enhanced(skeleton, dialog, row.get('dialog_id'))
                    if isinstance(result_viz, tuple):
                        fig, blocks, block_types = result_viz
                        st.plotly_chart(fig, use_container_width=True)
                        
                        st.write("🔍 **Детали блоков:**")
                        selected_block = st.selectbox("Выберите блок для просмотра", 
                                                    [f"{i+1}. {b} ({t})" for i, (b,t) in enumerate(zip(blocks, block_types))])

                        if selected_block:
                            idx = int(selected_block.split('.')[0]) - 1
                            block_name = blocks[idx]
                            block_type = block_types[idx]
                            
                            depth = sum(1 for b in blocks[:idx+1] if b.startswith('action') or b.startswith('bossaction'))
                            
                            dialog_snippet = ""
                            if dialog and pd.notna(dialog):
                                dialog_lines = dialog.split('\n')
                                total_lines = len(dialog_lines)
                                lines_per_block = max(1, total_lines // len(blocks))
                                
                                start_line = idx * lines_per_block
                                end_line = min(start_line + lines_per_block + 5, total_lines)
                                
                                snippet_lines = dialog_lines[start_line:end_line]
                                dialog_snippet = '\n'.join(snippet_lines)
                            
                            with st.expander(f"📋 Блок #{idx+1}: {block_name}", expanded=True):
                                st.write(f"**Тип:** {block_type}")
                                st.write(f"**Глубина:** {depth}")
                                st.write(f"**Порядок:** {idx + 1} из {len(blocks)}")
                                
                                if dialog_snippet:
                                    st.write("**📄 Фрагмент диалога для этого блока:**")
                                    st.code(dialog_snippet, language='text')
                                else:
                                    st.info("ℹ️ Диалог не доступен")
                    else:
                        st.warning(result_viz)
                else:
                    st.warning("❌ Диалог не найден")
            
            st.divider()
            st.write("📚 Браузер диалогов")
            if 'dialog_id' in skel_df.columns:
                display_cols = [c for c in ['dialog_id', 'Скелет', 'Тег', 'dialog_grade', 'dialog_role'] if c in skel_df.columns]
                st.dataframe(skel_df[display_cols].head(50), use_container_width=True)
            else:
                st.info("ℹ️ Столбец dialog_id не найден")
        else:
            st.info("ℹ️ Нет диалогов, подходящих под текущие фильтры (или данные по скелетам не загружены)")

    with tab4:
        st.subheader("📚 Распределение по классам и предметам")

        # === 🆕 Класс УЧЕНИКА (dialog_grade) — из каких классов реально приходят дети ===
        # Это не то же самое, что 'Класс' ниже (тот — класс ПОДОБРАННОЙ темы).
        # dialog_grade диапазон 1-12 (не только 5-11), поэтому считаем отдельно
        # и по ВСЕМ диалогам (в рамках текущих фильтров), а не только по успешно смэтченным.
        if 'dialog_grade' in df.columns and df['dialog_grade'].notna().any():
            st.write("### 🎓 Класс ученика — откуда реально приходят дети")
            grade_counts = (
                df['dialog_grade']
                .dropna()
                .astype(str)
                .value_counts()
                .reset_index()
            )
            grade_counts.columns = ['Класс ученика', 'Количество диалогов']

            # сортируем по номеру класса, а не по алфавиту (иначе '10' окажется перед '2')
            def _grade_sort_key(v):
                try:
                    return int(v)
                except (ValueError, TypeError):
                    return 999
            grade_counts['_sort'] = grade_counts['Класс ученика'].apply(_grade_sort_key)
            grade_counts = grade_counts.sort_values('_sort').drop(columns='_sort')

            fig_dialog_grade = px.bar(
                grade_counts, x='Класс ученика', y='Количество диалогов',
                color='Класс ученика', title="Диалоги по классу ученика (dialog_grade)",
                text_auto='.0f', color_discrete_sequence=px.colors.qualitative.Set3
            )
            fig_dialog_grade.update_layout(height=400, showlegend=False, xaxis={'type': 'category'})
            st.plotly_chart(fig_dialog_grade, use_container_width=True)
            st.caption(
                f"Всего диалогов с указанным классом ученика: {grade_counts['Количество диалогов'].sum():,} "
                f"из {len(df):,} (в рамках текущих фильтров)"
            )
            st.divider()

        if 'class_stats' in data and not data['class_stats'].empty:
            class_df = data['class_stats']
            if 'Класс' in class_df.columns and 'Количество учеников' in class_df.columns:
                st.write("### 📖 Класс подобранной темы")
                st.caption("Это класс темы, которую подобрал алгоритм мэтчинга — не обязательно совпадает с реальным классом ученика выше.")
                fig_class = px.bar(class_df.sort_values('Класс'), x='Класс', y='Количество учеников',
                                  color='Класс', title="Диалоги по классу темы", text_auto='.0f')
                fig_class.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig_class, use_container_width=True)
            if 'Предмет' in class_df.columns:
                subject_counts = class_df.groupby('Предмет')['Количество учеников'].sum().reset_index()
                fig_subject = px.pie(subject_counts, values='Количество учеников', names='Предмет',
                                    title="Распределение по предметам", hole=0.4)
                st.plotly_chart(fig_subject, use_container_width=True)
        else:
            st.info("ℹ️ Данные по классам/предметам не загружены")

        # === 🆕 Частотность запросов (initial_topic) ===
        # Свободный текст темы, введённый учеником/пришедший из журнала — уникальных
        # формулировок тысячи, поэтому показываем не список всех, а частотный топ.
        # Уважает текущие фильтры (df уже отфильтрован выше по всем полям сайдбара).
        if 'initial_topic' in df.columns and df['initial_topic'].notna().any():
            st.divider()
            st.write("### 📝 Частотность запросов (исходная тема)")
            topic_counts = df['initial_topic'].dropna().value_counts()
            topic_counts = topic_counts[topic_counts.index.astype(str).str.strip() != '']

            if not topic_counts.empty:
                col_a, col_b = st.columns(2)
                with col_a:
                    st.metric("Заполнено тем", f"{topic_counts.sum():,}", f"из {len(df):,} диалогов")
                with col_b:
                    st.metric("Уникальных формулировок", f"{len(topic_counts):,}")

                top_n = st.slider("Сколько тем показать", 5, 50, 15, key='top_topics_n')
                top_topics_df = topic_counts.head(top_n).reset_index()
                top_topics_df.columns = ['Тема', 'Количество']

                fig_topics = px.bar(
                    top_topics_df.sort_values('Количество'),
                    x='Количество', y='Тема', orientation='h',
                    title=f"Топ-{top_n} самых частых тем", text_auto='.0f'
                )
                fig_topics.update_layout(height=max(300, top_n * 25), showlegend=False)
                st.plotly_chart(fig_topics, use_container_width=True)
            else:
                st.info("ℹ️ В текущей выборке нет заполненных тем (все диалоги — с баннера)")
    
    with tab5:
        st.subheader("⏱️ Метрики времени сессий")
        time_cols = [c for c in df.columns if 'Время' in c or 'Сообщений' in c]
        if time_cols:
            col1, col2, col3, col4 = st.columns(4)
            if 'Время сессии в секундах' in df.columns:
                with col1: st.metric("⏱️ Среднее время", f"{df['Время сессии в секундах'].mean()/60:.1f} мин")
                with col2: st.metric("📊 Медиана", f"{df['Время сессии в секундах'].median()/60:.1f} мин")
            if 'Сообщений ученика' in df.columns:
                with col3: st.metric("💬 Ср. сообщений (ученик)", f"{df['Сообщений ученика'].mean():.1f}")
            if 'Сообщений тьютора' in df.columns:
                with col4: st.metric("🤖 Ср. сообщений (бот)", f"{df['Сообщений тьютора'].mean():.1f}")
            
            st.divider()
            st.subheader("🏆 Топ самых длинных сессий")
            
            if 'Время сессии в секундах' in df.columns and 'dialog_id' in df.columns:
                top_sessions = df.nlargest(20, 'Время сессии в секундах')[
                    ['dialog_id', 'Время сессии в секундах', 'dialog_grade', 'dialog_role', 'product_slug']
                ].copy()
                
                top_sessions['Время (мин)'] = (top_sessions['Время сессии в секундах'] / 60).round(1)
                
                st.write(f"💡 **Кликни по ID диалога, чтобы открыть его скелет**")
                
                for idx, row in top_sessions.iterrows():
                    col1, col2, col3, col4, col5 = st.columns([3, 1, 1, 1, 1])
                    with col1:
                        st.text(f"🔗 {row['dialog_id'][:36]}...")
                    with col2:
                        st.metric("", f"{row['Время (мин)']} мин")
                    with col3:
                        st.text(f"Класс {row['dialog_grade']}")
                    with col4:
                        st.text(row['dialog_role'][:10])
                    with col5:
                        st.text(row['product_slug'][:10] if pd.notna(row['product_slug']) else "")
                
                selected_dialog = st.selectbox(
                    " Выбери диалог для просмотра скелета",
                    options=top_sessions['dialog_id'].tolist(),
                    format_func=lambda x: f"{x[:36]}... ({top_sessions[top_sessions['dialog_id']==x]['Время (мин)'].iloc[0] if len(top_sessions[top_sessions['dialog_id']==x]) > 0 else '?'} мин)"
                )
                
                if selected_dialog:
                    st.session_state['selected_dialog_id'] = selected_dialog
                    st.success(f"✅ ID {selected_dialog[:36]}... скопирован! Перейди на вкладку **🦴 Скелеты**")
        else:
            st.info("ℹ️ Столбцы с метриками времени не найдены")
    
    with tab6:
        st.subheader("🔄 Сравнение месяцев")

        # Начальное значение задаём через session_state ДО создания виджета,
        # и только один раз — иначе конфликт с default= у multiselect ниже
        # (Streamlit не разрешает одновременно default= и прямую запись в session_state[key])
        if 'compare_months' not in st.session_state:
            st.session_state['compare_months'] = ['Январь', 'Февраль']

        compare_all = st.checkbox(
            "🗓 Сравнить все месяцы сразу",
            key='compare_all_toggle',
            help="Включи, чтобы не проставлять галочки вручную — подставятся все месяцы в хронологическом порядке."
        )
        if compare_all:
            st.session_state['compare_months'] = MONTH_ORDER[:]

        selected_months = st.multiselect(
            "Выберите месяцы для сравнения",
            MONTH_ORDER,
            key='compare_months',
        )

        if compare_all:
            st.caption(
                f"🗓 Режим «все месяцы»: сравниваем **{len(selected_months)}** мес. — "
                f"{' → '.join(selected_months)}"
            )
        elif selected_months:
            st.caption(f"Выбрано месяцев: **{len(selected_months)}**")

        if len(selected_months) >= 2:
            with st.spinner('Загрузка данных для сравнения...'):
                df_combined = load_multiple_months(selected_months)

            if not df_combined.empty:
                if 'Месяц' in df_combined.columns:
                    present_order = [m for m in MONTH_ORDER if m in df_combined['Месяц'].unique()]
                    df_combined['Месяц'] = pd.Categorical(
                        df_combined['Месяц'], categories=present_order, ordered=True
                    )

                if selected_grade != 'Все' and 'dialog_grade' in df_combined.columns:
                    df_combined = df_combined[df_combined['dialog_grade'].astype(str) == selected_grade]

                if selected_role != 'Все' and 'dialog_role' in df_combined.columns:
                    df_combined = df_combined[df_combined['dialog_role'] == selected_role]

                if selected_product != 'Все' and 'product_slug' in df_combined.columns:
                    df_combined = df_combined[df_combined['product_slug'] == selected_product]

                if selected_source != 'Все' and 'источник_лист' in df_combined.columns:
                    df_combined = df_combined[df_combined['источник_лист'] == selected_source]

                if entry_source_filter != 'Все' and 'initial_topic' in df_combined.columns:
                    if entry_source_filter == '📓 Из журнала (тема есть)':
                        df_combined = df_combined[
                            df_combined['initial_topic'].notna() & (df_combined['initial_topic'].astype(str).str.strip() != '')
                        ]
                    else:
                        df_combined = df_combined[
                            df_combined['initial_topic'].isna() | (df_combined['initial_topic'].astype(str).str.strip() == '')
                        ]

                st.info(f"🔍 В сравнении участвует {len(df_combined):,} диалогов (после фильтров)")

                st.write("### 📊 Конверсия по месяцам")

                if 'status_only' in df_combined.columns:
                    monthly_success = df_combined[df_combined['status_only'] == '(-) всё хорошо'].groupby('Месяц').size()
                    monthly_total = df_combined.groupby('Месяц').size()
                    conversion_rate = (monthly_success / monthly_total * 100).round(1)

                    conv_df = pd.DataFrame({
                        'Месяц': conversion_rate.index,
                        'Конверсия (%)': conversion_rate.values,
                        'Всего': monthly_total.values,
                        'Успешные': monthly_success.values
                    })

                    fig_conv = px.bar(conv_df, x='Месяц', y='Конверсия (%)', color='Месяц',
                                     title="Конверсия: успешный мэтчинг / всего диалогов",
                                     text_auto='.1f%', color_discrete_sequence=px.colors.qualitative.Pastel)
                    fig_conv.update_layout(height=400, showlegend=False)
                    st.plotly_chart(fig_conv, use_container_width=True)

                    st.dataframe(conv_df.style.format({'Конверсия (%)': '{:.1f}%'}), use_container_width=True)

                if 'activity_dt' in df_combined.columns:
                    st.write("### 📈 Динамика по дням (все выбранные месяцы)")
                    df_combined['date'] = pd.to_datetime(df_combined['activity_dt']).dt.date
                    daily = df_combined.groupby(['Месяц', 'date']).size().reset_index(name='Количество')

                    fig_trend = px.line(daily, x='date', y='Количество', color='Месяц',
                                       title="Количество диалогов по дням", markers=True)
                    fig_trend.update_layout(height=400, hovermode='x unified')
                    st.plotly_chart(fig_trend, use_container_width=True)
            else:
                st.warning("⚠️ Не удалось загрузить данные для сравнения")
        else:
            st.info("ℹ️ Выберите минимум 2 месяца для сравнения")

    with tab7:
        st.subheader(f"🔀 Сравнение источников — {selected_month}")

        if 'chart' not in data or data['chart'].empty or 'источник_лист' not in data['chart'].columns:
            st.info("ℹ️ В этом месяце нет данных по источникам.")
        else:
            df_src = data['chart'].copy()

            if selected_grade != 'Все' and 'dialog_grade' in df_src.columns:
                df_src = df_src[df_src['dialog_grade'].astype(str) == selected_grade]
            if selected_role != 'Все' and 'dialog_role' in df_src.columns:
                df_src = df_src[df_src['dialog_role'] == selected_role]
            if selected_product != 'Все' and 'product_slug' in df_src.columns:
                df_src = df_src[df_src['product_slug'] == selected_product]
            if date_range and 'activity_dt' in df_src.columns:
                df_src['activity_dt'] = pd.to_datetime(df_src['activity_dt'], errors='coerce')
                df_src = df_src[(df_src['activity_dt'].dt.date >= date_range[0]) & (df_src['activity_dt'].dt.date <= date_range[1])]
            if entry_source_filter != 'Все' and 'initial_topic' in df_src.columns:
                if entry_source_filter == '📓 Из журнала (тема есть)':
                    df_src = df_src[df_src['initial_topic'].notna() & (df_src['initial_topic'].astype(str).str.strip() != '')]
                else:
                    df_src = df_src[df_src['initial_topic'].isna() | (df_src['initial_topic'].astype(str).str.strip() == '')]

            available_sources = sorted(df_src['источник_лист'].dropna().unique().tolist())

            if len(available_sources) < 2:
                st.info(f"ℹ️ В {selected_month} доступен только один источник ({available_sources[0] if available_sources else '—'}) — сравнивать не с чем.")
            else:
                selected_sources_compare = st.multiselect(
                    "Источники для сравнения", available_sources, default=available_sources
                )
                df_src = df_src[df_src['источник_лист'].isin(selected_sources_compare)]

                st.info(f"🔍 В сравнении участвует {len(df_src):,} диалогов")

                if 'status_only' in df_src.columns and len(df_src) > 0:
                    st.write("### 📊 Конверсия успешного мэтчинга по источникам")

                    src_success = df_src[df_src['status_only'] == '(-) всё хорошо'].groupby('источник_лист').size()
                    src_total = df_src.groupby('источник_лист').size()
                    src_conv = (src_success / src_total * 100).round(1)

                    src_conv_df = pd.DataFrame({
                        'Источник': src_total.index,
                        'Всего': src_total.values,
                        'Успешные': src_success.reindex(src_total.index).fillna(0).astype(int).values,
                    })
                    src_conv_df['Конверсия (%)'] = (src_conv_df['Успешные'] / src_conv_df['Всего'] * 100).round(1)

                    fig_src_conv = px.bar(
                        src_conv_df, x='Источник', y='Конверсия (%)', color='Источник',
                        title="Конверсия: успешный мэтчинг / всего диалогов, по источникам",
                        text_auto='.1f%', color_discrete_sequence=px.colors.qualitative.Set2
                    )
                    fig_src_conv.update_layout(height=400, showlegend=False)
                    st.plotly_chart(fig_src_conv, use_container_width=True)

                    st.dataframe(
                        src_conv_df.style.format({'Конверсия (%)': '{:.1f}%'}),
                        use_container_width=True
                    )

                if 'status_only_post' in df_src.columns and 'status_only' in df_src.columns:
                    success_src_df = df_src[df_src['status_only'] == '(-) всё хорошо']
                    if len(success_src_df) > 0:
                        st.write("### 🎯 Дошли до финальной задачи (из успешных), по источникам")
                        solved_by_src = success_src_df[
                            success_src_df['status_only_post'] == '(-) решили финальную задачу'
                        ].groupby('источник_лист').size()
                        success_total_by_src = success_src_df.groupby('источник_лист').size()
                        solved_rate = (solved_by_src.reindex(success_total_by_src.index).fillna(0) / success_total_by_src * 100).round(1)

                        solved_df = pd.DataFrame({
                            'Источник': success_total_by_src.index,
                            'Успешных мэтчингов': success_total_by_src.values,
                            'Решили финал (%)': solved_rate.values
                        })

                        fig_solved = px.bar(
                            solved_df, x='Источник', y='Решили финал (%)', color='Источник',
                            title="% решивших финальную задачу от успешно смэтченных",
                            text_auto='.1f%', color_discrete_sequence=px.colors.qualitative.Set2
                        )
                        fig_solved.update_layout(height=350, showlegend=False)
                        st.plotly_chart(fig_solved, use_container_width=True)

                st.write("### ⏱️ Время и сообщения по источникам")
                agg_cols = {}
                if 'Время сессии в секундах' in df_src.columns:
                    agg_cols['Время сессии в секундах'] = 'mean'
                if 'Сообщений ученика' in df_src.columns:
                    agg_cols['Сообщений ученика'] = 'mean'
                if 'Сообщений тьютора' in df_src.columns:
                    agg_cols['Сообщений тьютора'] = 'mean'

                if agg_cols:
                    src_time_stats = df_src.groupby('источник_лист').agg(agg_cols).round(1).reset_index()
                    if 'Время сессии в секундах' in src_time_stats.columns:
                        src_time_stats['Время сессии, мин'] = (src_time_stats['Время сессии в секундах'] / 60).round(1)
                        src_time_stats = src_time_stats.drop(columns=['Время сессии в секундах'])
                    src_time_stats = src_time_stats.rename(columns={'источник_лист': 'Источник'})
                    st.dataframe(src_time_stats, use_container_width=True)

                if 'activity_dt' in df_src.columns:
                    st.write("### 📈 Динамика диалогов по дням, по источникам")
                    df_src['date'] = pd.to_datetime(df_src['activity_dt'], errors='coerce').dt.date
                    daily_src = df_src.groupby(['источник_лист', 'date']).size().reset_index(name='Количество')
                    daily_src = daily_src.rename(columns={'источник_лист': 'Источник'})

                    fig_src_trend = px.line(
                        daily_src, x='date', y='Количество', color='Источник',
                        title="Количество диалогов по дням, по источникам", markers=True
                    )
                    fig_src_trend.update_layout(height=400, hovermode='x unified')
                    st.plotly_chart(fig_src_trend, use_container_width=True)

    st.divider()
    col1, col2 = st.columns([3, 1])
    with col2:
        st.download_button("📥 Скачать отчёт (CSV)", data=df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
                          file_name=f"analytics_{selected_month}_{datetime.now().strftime('%Y%m%d')}.csv",
                          mime="text/csv", use_container_width=True)

if __name__ == "__main__":
    main()
