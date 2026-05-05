import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import re
import time

# Настройка страницы
st.set_page_config(
    page_title="R&D Аналитика: Мэтчинг",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# === КОНФИГУРАЦИЯ: ССЫЛКИ НА ТАБЛИЦЫ ===
MONTHS = {
    'Январь': {
        'base_url': 'https://docs.google.com/spreadsheets/d/1ypPA0yGpFEPt8LilW45l6GMRDbBwGMhzCWWAN8iSE_c',
        'sheets': {
            'chart_data': {'gid': 261155966, 'name': 'Chart data'},
            'stats': {'gid': 1180470564, 'name': 'Общая статистика'},
            'class_stats': {'gid': 1842481687, 'name': 'Статистика'},
            'skeleton': {'gid': 5709328, 'name': 'Скелет'}
        }
    },
    'Февраль': {
        'base_url': 'https://docs.google.com/spreadsheets/d/1Qj7znA3CDCodX8drqmxieTG7nxLEFukwIN4q3woOUzQ',
        'sheets': {
            'chart_data': {'gid': 1513590084, 'name': 'Chart data'},
            'stats': {'gid': 383989852, 'name': 'Общая статистика'},
            'class_stats': {'gid': 1439000990, 'name': 'Статистика'},
            'skeleton': {'gid': 699313907, 'name': 'Скелет'}
        }
    },
    'Март': {
        'base_url': 'https://docs.google.com/spreadsheets/d/1USRFN4v9zEnY-UEoHVbSJ9I0QWx3aznWT_sbRqXWQBs',
        'sheets': {
            'chart_data': {'gid': 2117408418, 'name': 'Chart data'},
            'stats': {'gid': 1182428016, 'name': 'Общая статистика'},
            'class_stats': {'gid': 1935468458, 'name': 'Статистика'},
            'skeleton': {'gid': 1169638280, 'name': 'Скелет'}
        }
    },
    'Апрель': {
        'base_url': 'https://docs.google.com/spreadsheets/d/1ZDH9o-pfVhVJdWRwSVrVsSIi77rMY44_3O482761mS0',
        'sheets': {
            'chart_data': {'gid': 1108293095, 'name': 'Chart data'},
            'stats': {'gid': 1774326465, 'name': 'Общая статистика'},
            'class_stats': {'gid': 210828249, 'name': 'Статистика'},
            'skeleton': {'gid': 575620294, 'name': 'Скелет'}
        }
    }
}

# === ФУНКЦИИ ЗАГРУЗКИ ДАННЫХ ===

@st.cache_data(ttl=3600)
def load_csv(base_url, gid):
    """Загружает лист Google Sheets как CSV с таймаутом"""
    url = f"{base_url}/export?format=csv&gid={gid}"
    try:
        import requests
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        from io import StringIO
        csv_data = StringIO(response.text)
        df = pd.read_csv(csv_data, encoding='utf-8-sig')
        return df
    except Exception as e:
        st.error(f"💥 Ошибка загрузки: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=3600)
def load_month_data(month_name):
    """Загружает данные из локальных CSV файлов"""
    month_prefix = {'Январь': 'jan', 'Февраль': 'feb', 'Март': 'mar', 'Апрель': 'apr'}
    prefix = month_prefix.get(month_name, 'jan')
    data = {}
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        status_text.text(f"📊 {month_name}: Загрузка основных данных...")
        try:
            data['chart'] = pd.read_csv(f'data/{prefix}_chart_data.csv', encoding='utf-8-sig')
        except FileNotFoundError:
            data['chart'] = pd.DataFrame()
        progress_bar.progress(25)
        
        status_text.text(f"📈 {month_name}: Загрузка статистики...")
        try:
            data['stats'] = pd.read_csv(f'data/{prefix}_stats.csv', encoding='utf-8-sig')
        except FileNotFoundError:
            data['stats'] = pd.DataFrame()
        progress_bar.progress(50)
        
        status_text.text(f"📚 {month_name}: Загрузка по классам...")
        try:
            data['class_stats'] = pd.read_csv(f'data/{prefix}_class_stats.csv', encoding='utf-8-sig')
        except FileNotFoundError:
            data['class_stats'] = pd.DataFrame()
        progress_bar.progress(75)
        
        status_text.text(f"🦴 {month_name}: Загрузка скелетов...")
        try:
            data['skeleton'] = pd.read_csv(f'data/{prefix}_skeleton.csv', encoding='utf-8-sig')
            
            # 🆕 Переименовываем status_only -> Скелет (если нужно)
            if 'skeleton' in data and not data['skeleton'].empty:
                if 'status_only' in data['skeleton'].columns and 'Скелет' not in data['skeleton'].columns:
                    data['skeleton'] = data['skeleton'].rename(columns={'status_only': 'Скелет'})
                    
        except FileNotFoundError:
            data['skeleton'] = pd.DataFrame()
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
    
    # 🎨 Яркая цветовая схема
    color_map = {
        'plan': '#2E86AB',        # Яркий синий
        'info': '#A23B72',        # Яркий розовый
        'action': '#F18F01',      # Яркий оранжевый
        'problem': '#C73E1D',     # Яркий красный
        'bossaction': '#6A994E',  # Яркий зелёный
        'summary': '#386641',     # Тёмно-зелёный
        'other': '#6C757D'        # Серый
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
        
        # 🔴 Увеличенные маркеры
        sizes.append(15)  # Было 8-13, стало 15
        
        # 💡 Подробный tooltip
        hover_text = f"<b style='font-size:14px'>{label}</b><br>"
        hover_text += f"Порядок: {i+1}<br>"
        hover_text += f"Глубина: {depth}<br>"
        if block_type == 'action':
            hover_text += f"Шаг: {step_num}"
        hover_texts.append(hover_text)
        
        # 🔤 Крупный текст на графике
        labels.append(f"{i+1}. {label}")
    
    # 📊 Строим график
    fig = go.Figure()
    
    # Основная линия (толще и ярче)
    fig.add_trace(go.Scatter(
        x=x_vals, y=y_vals,
        mode='lines+markers+text',
        line=dict(color='#2E86AB', width=4),  # Толщина 4 вместо 2
        marker=dict(
            size=sizes,  # 15 вместо 8-13
            color=colors,
            line=dict(width=2, color='white'),  # Белая обводка
            opacity=1.0  # Полная непрозрачность
        ),
        text=labels,
        textposition='top center',
        textfont=dict(size=12, color='#333333', family='Arial Black'),  # Жирный тёмный текст
        hovertext=hover_texts,
        hoverinfo='text',
        name='Скелет',
        hoverlabel=dict(bgcolor='white', font_size=14, font_family='Arial')
    ))
    
    # Вертикальные линии-скачки (более заметные)
    for i in range(len(blocks)-1):
        if blocks[i].startswith('action') and not blocks[i+1].startswith('action'):
            fig.add_trace(go.Scatter(
                x=[i, i], y=[y_vals[i], 0],
                mode='lines',
                line=dict(color='#FF6B6B', width=2, dash='dash'),  # Красный пунктир
                showlegend=False, hoverinfo='skip', name='Переход'
            ))
    
    # 🔴 Подсветка последнего блока (место отвала)
    if len(blocks) > 0:
        last_type = block_types[-1]
        if last_type in ['action', 'bossaction']:
            fig.add_trace(go.Scatter(
                x=[len(blocks)-1], y=[y_vals[-1]],
                mode='markers',
                marker=dict(size=20, color='red', symbol='x', line=dict(width=3, color='darkred')),
                name='🔴 Отвал здесь', hoverinfo='skip', showlegend=True
            ))
    
    # 🎨 Улучшенный layout
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
        height=500,  # Чуть выше
        hovermode='closest',
        yaxis=dict(
            autorange='reversed', 
            title='Шаг ↓', 
            showgrid=True, 
            gridcolor='rgba(0,0,0,0.15)',  # Темнее сетка
            zerolinecolor='#333',
            zerolinewidth=2
        ),
        xaxis=dict(
            showgrid=True,
            gridcolor='rgba(0,0,0,0.05)'
        ),
        plot_bgcolor='white',  # Белый фон
        paper_bgcolor='white',
        legend=dict(
            orientation='h', 
            yanchor='bottom', 
            y=1.02, 
            xanchor='right', 
            x=1,
            font=dict(size=12)
        ),
        margin=dict(l=60, r=60, t=80, b=60)  # Больше отступов
    )
    
    return fig, blocks, block_types

# === 🆕 ФУНКЦИЯ ДЛЯ СРАВНЕНИЯ МЕСЯЦЕВ ===

def load_multiple_months(month_list):
    """Загружает данные для нескольких месяцев для сравнения"""
    combined = {}
    for month in month_list:
        data = load_month_data(month)
        if 'chart' in data and not data['chart'].empty:
            data['chart']['Месяц'] = month
            combined[month] = data['chart']
    return pd.concat(combined.values(), ignore_index=True) if combined else pd.DataFrame()

# === ОСНОВНОЙ ИНТЕРФЕЙС ===

def main():
    st.title("📊 R&D Аналитика: Мэтчинг")
    st.markdown("Визуализация данных по диалогам за январь–апрель 2026")
    
    # === САЙДБАР: ФИЛЬТРЫ ===
    with st.sidebar:
        st.header("🎛 Фильтры")
        
        selected_month = st.selectbox("📅 Месяц", list(MONTHS.keys()), index=0)
        
        with st.spinner('Загрузка данных...'):
            data = load_month_data(selected_month)
        
        # === Фильтр по классу ===
        selected_grade = 'Все'
        if 'chart' in data and not data['chart'].empty and 'dialog_grade' in data['chart'].columns:
            grades = ['Все'] + sorted(data['chart']['dialog_grade'].dropna().unique().astype(str).tolist())
            selected_grade = st.selectbox("📚 Класс", grades)
        
        # === 🆕 Фильтр по роли ===
        selected_role = 'Все'
        if 'chart' in data and not data['chart'].empty and 'dialog_role' in data['chart'].columns:
            roles = ['Все'] + sorted(data['chart']['dialog_role'].dropna().unique().tolist())
            selected_role = st.selectbox("👤 Роль", roles)
        
        # === Фильтр по периоду ===
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

    # === ПРОВЕРКА ЗАГРУЗКИ ===
    if 'chart' not in data or data['chart'].empty:
        st.warning("⚠️ Не удалось загрузить данные.")
        return

    # === ПРИМЕНЯЕМ ФИЛЬТРЫ КО ВСЕМ ДАННЫМ ===
    df = data['chart'].copy()
    filter_mask = pd.Series([True] * len(df), index=df.index)

    # Фильтр по классу
    if selected_grade != 'Все' and 'dialog_grade' in df.columns:
        filter_mask &= df['dialog_grade'].astype(str) == selected_grade

    # 🆕 Фильтр по роли
    if selected_role != 'Все' and 'dialog_role' in df.columns:
        filter_mask &= df['dialog_role'] == selected_role

    # Фильтр по периоду
    if date_range and 'activity_dt' in df.columns:
        filter_mask &= (df['activity_dt'].dt.date >= date_range[0]) & \
                    (df['activity_dt'].dt.date <= date_range[1])

    # Применяем фильтр к основным данным
    df = df[filter_mask].copy()

    # 🆕 Применяем фильтр к скелетам (если есть dialog_id в chart и skeleton)
    if 'skeleton' in data and not data['skeleton'].empty and 'dialog_id' in data['skeleton'].columns:
        filtered_ids = df['dialog_id'].unique() if 'dialog_id' in df.columns else []
        if len(filtered_ids) > 0:
            data['skeleton'] = data['skeleton'][data['skeleton']['dialog_id'].isin(filtered_ids)].copy()

    # Показываем результат фильтрации
    if len(df) < len(data['chart']):
        filters_applied = []
        if selected_grade != 'Все':
            filters_applied.append(f"класс {selected_grade}")
        if selected_role != 'Все':
            filters_applied.append(f"роль: {selected_role}")
        if date_range:
            filters_applied.append(f"период: {date_range[0]} - {date_range[1]}")
        
        st.info(f"🔍 Показано {len(df):,} из {len(data['chart']):,} диалогов (фильтры: {', '.join(filters_applied)})")
    
    # === ВКЛАДКИ ===
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📊 Воронки", "📈 Динамика", "🦴 Скелеты", 
        "📚 Классы/Предметы", "⏱️ Время", "🔄 Сравнение"
    ])
    
    # === ВКЛАДКА 1: ВОРОНКИ (ДИНАМИЧЕСКИЕ) ===
    with tab1:
        st.subheader("🔄 Воронка мэтчинга")
        
        # Проверяем, есть ли у нас данные после фильтров
        if len(df) == 0:
            st.warning("⚠️ Нет данных для отображения воронки с текущими фильтрами.")
        elif 'status_only' in df.columns:
            #  ГЛАВНАЯ ВОРОНКА
            # Считаем количество каждого тега в отфильтрованных данных
            funnel_counts = df['status_only'].value_counts()
            
            # Маппинг тегов на понятные названия
            tag_mapping = {
                '(-) всё хорошо': '✅ Успешный мэтчинг',
                '(-) ничего не ввели': '❌ Пустой запрос',
                '(-) ввели задачу': '❓ Задача вместо темы',
                '(-) ввели тему не по математике': '🌍 Не математика',
                '(-) ввели мат тему не 5-11': '🔢 Не 5-11 класс',
                '(-) ушли после мэтчинга': '🚶 Ушли после подбора',
                '(-) мэтчинг не сработал': '⚠️ Ошибка подбора'
            }
            
            # Формируем данные для графика
            funnel_data = []
            for tag, label in tag_mapping.items():
                if tag in funnel_counts.index:
                    funnel_data.append({'Этап': label, 'Количество': int(funnel_counts[tag])})
            
            # Строим график (plotly сам отсортирует от большего к меньшему)
            if funnel_data:
                filter_info = f" (после фильтров: {len(df):,} диалогов)" if len(df) < len(data['chart']) else ""
                
                fig_funnel = px.funnel(
                    pd.DataFrame(funnel_data), 
                    x='Количество', 
                    y='Этап',
                    title=f"Воронка мэтчинга — {selected_month}{filter_info}",
                    color='Этап',
                    color_discrete_sequence=px.colors.qualitative.Set2
                )
                fig_funnel.update_layout(height=500, showlegend=False)
                st.plotly_chart(fig_funnel, use_container_width=True)
            
            # 🔵 ВОРОНКА ПОСТ-МЭТЧИНГА (из столбца status_only_post)
            st.divider()
            st.subheader("🎯 Пост-мэтчинг: углубление в диалог")
            
            if 'status_only_post' in df.columns:
                # Берем только успешные диалоги (где status_only == '(-) всё хорошо'), 
                # чтобы показать, что было дальше
                successful_df = df[df['status_only'] == '(-) всё хорошо']
                
                if len(successful_df) > 0:
                    post_counts = successful_df['status_only_post'].value_counts()
                    
                    post_tags = {
                        '(-) решили финальную задачу': '🏆 Решили финал',
                        '(-) увидели финальную задачу': '🎯 Увидели финал',
                        '(-) ушли после первого шага': '🚶 Шаг 1',
                        '(-) ушли после 2-го и более шага': '🚶 2+ шага',
                        '(-) ушли после плана': '📋 Увидели план'
                    }
                    
                    post_data = []
                    for tag, label in post_tags.items():
                        if tag in post_counts.index:
                            post_data.append({'Этап': label, 'Количество': int(post_counts[tag])})
                    
                    if post_data:
                        fig_post = px.funnel(
                            pd.DataFrame(post_data),
                            x='Количество',
                            y='Этап',
                            title="Воронка пост-мэтчинга (только успешные)",
                            color='Этап',
                            color_discrete_sequence=px.colors.sequential.Blues
                        )
                        fig_post.update_layout(height=400, showlegend=False)
                        st.plotly_chart(fig_post, use_container_width=True)
                else:
                    st.info("ℹ️ Нет успешных диалогов для анализа пост-мэтчинга.")
            else:
                st.warning("⚠️ Столбец 'status_only_post' не найден в данных.")

        else:
            st.error("❌ В данных не найден столбец 'status_only'. Проверь структуру CSV файла.")
    
    # === ВКЛАДКА 2: ДИНАМИКА ПО ДАТАМ ===
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
    
    # === 🆕 ВКЛАДКА 3: СКЕЛЕТЫ ДИАЛОГОВ (УЛУЧШЕННЫЕ) ===
    with tab3:
        st.subheader("🦴 Визуализация скелетов диалогов")
        
        if 'skeleton' in data and not data['skeleton'].empty:
            skel_df = data['skeleton']
            
            # Поиск диалога
            search_id = st.text_input("🔍 ID диалога (или часть)", placeholder="003a9e82...")
            
            if search_id and 'dialog_id' in skel_df.columns:
                result = skel_df[skel_df['dialog_id'].astype(str).str.contains(search_id, case=False)]
                if not result.empty:
                    row = result.iloc[0]
                    skeleton = row.get('Скелет', row.get('скелет', ''))
                    dialog = row.get('Диалог', '')
                    
                    # Визуализация с деталями
                    result_viz = visualize_skeleton_enhanced(skeleton, dialog, row.get('dialog_id'))
                    if isinstance(result_viz, tuple):
                        fig, blocks, block_types = result_viz
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # 🆕 Интерактивные детали при выборе блока
                        st.write("🔍 **Детали блоков:**")
                        selected_block = st.selectbox("Выберите блок для просмотра", 
                                                    [f"{i+1}. {b} ({t})" for i, (b,t) in enumerate(zip(blocks, block_types))])

                        if selected_block:
                            idx = int(selected_block.split('.')[0]) - 1
                            block_name = blocks[idx]
                            block_type = block_types[idx]
                            
                            # 📊 Расчёт глубины
                            depth = sum(1 for b in blocks[:idx+1] if b.startswith('action') or b.startswith('bossaction'))
                            
                            # 📝 Разбиваем диалог на части по количеству блоков
                            dialog_snippet = ""
                            if dialog and pd.notna(dialog):
                                dialog_lines = dialog.split('\n')
                                total_lines = len(dialog_lines)
                                lines_per_block = max(1, total_lines // len(blocks))
                                
                                # Вычисляем начало и конец фрагмента для этого блока
                                start_line = idx * lines_per_block
                                end_line = min(start_line + lines_per_block + 5, total_lines)  # +5 для контекста
                                
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
            
            # Браузер всех скелетов
            st.divider()
            st.write("📚 Браузер диалогов")
            if 'dialog_id' in skel_df.columns:
                display_cols = [c for c in ['dialog_id', 'Скелет', 'Тег', 'dialog_grade', 'dialog_role'] if c in skel_df.columns]
                st.dataframe(skel_df[display_cols].head(50), use_container_width=True)
            else:
                st.info("ℹ️ Столбец dialog_id не найден")
        else:
            st.info("ℹ️ Данные по скелетам не загружены")
    
    # === ВКЛАДКА 4: КЛАССЫ / ПРЕДМЕТЫ ===
    with tab4:
        st.subheader("📚 Распределение по классам и предметам")
        if 'class_stats' in data and not data['class_stats'].empty:
            class_df = data['class_stats']
            if 'Класс' in class_df.columns and 'Количество учеников' in class_df.columns:
                fig_class = px.bar(class_df.sort_values('Класс'), x='Класс', y='Количество учеников',
                                  color='Класс', title="Диалоги по классам", text_auto='.0f')
                fig_class.update_layout(height=400, showlegend=False)
                st.plotly_chart(fig_class, use_container_width=True)
            if 'Предмет' in class_df.columns:
                subject_counts = class_df.groupby('Предмет')['Количество учеников'].sum().reset_index()
                fig_subject = px.pie(subject_counts, values='Количество учеников', names='Предмет',
                                    title="Распределение по предметам", hole=0.4)
                st.plotly_chart(fig_subject, use_container_width=True)
        else:
            st.info("ℹ️ Данные по классам/предметам не загружены")
    
    # === ВКЛАДКА 5: ВРЕМЯ СЕССИЙ ===
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
            
            if 'Время сессии в секундах' in df.columns:
                st.divider()
                df_clean = df[df['Время сессии в секундах'] > 0].copy()
                df_clean['Время (мин)'] = df_clean['Время сессии в секундах'] / 60
                fig_hist = px.histogram(df_clean, x='Время (мин)', nbins=30, title="Гистограмма длительности сессий", marginal='box')
                fig_hist.update_layout(height=400)
                st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.info("ℹ️ Столбцы с метриками времени не найдены")
    
    # === 🆕 ВКЛАДКА 6: СРАВНЕНИЕ МЕСЯЦЕВ ===
    with tab6:
        st.subheader("🔄 Сравнение месяцев")
        
        # Мультивыбор месяцев
        selected_months = st.multiselect("Выберите месяцы для сравнения", list(MONTHS.keys()), default=['Январь', 'Февраль'])
        
        if len(selected_months) >= 2:
            with st.spinner('Загрузка данных для сравнения...'):
                df_combined = load_multiple_months(selected_months)
            
            if not df_combined.empty:
                # 1. Сравнение воронок
                st.write("### 📊 Конверсия по месяцам")
                
                # Подсчёт успешных по месяцам
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
                    
                    # Таблица с деталями
                    st.dataframe(conv_df.style.format({'Конверсия (%)': '{:.1f}%'}), use_container_width=True)
                
                # 2. График тренда
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
    
    # === ЭКСПОРТ ===
    st.divider()
    col1, col2 = st.columns([3, 1])
    with col2:
        st.download_button("📥 Скачать отчёт (CSV)", data=df.to_csv(index=False, encoding='utf-8-sig').encode('utf-8-sig'),
                          file_name=f"analytics_{selected_month}_{datetime.now().strftime('%Y%m%d')}.csv",
                          mime="text/csv", use_container_width=True)

if __name__ == "__main__":
    main()