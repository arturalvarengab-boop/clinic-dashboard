import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
import base64
import calendar
import math

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Dashboard Clínica",
    page_icon="🏥",
    layout="wide",
)

BASE_URL = "https://api.clinicorp.com/rest/v1"


# ── UTILS ──────────────────────────────────────────────────────────────────────
def fmt_brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _auth(access_id: str, token: str) -> dict:
    encoded = base64.b64encode(f"{access_id}:{token}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


def nth_month_back(base, n):
    m, y = base.month - n, base.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def find_col(df, keywords):
    return next(
        (c for c in df.columns if any(k in c.lower() for k in keywords)), None
    )


# ── API ─────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def _get(endpoint: str, params: dict, access_id: str, token: str):
    url = f"{BASE_URL}{endpoint}"
    try:
        r = requests.get(url, params=params, headers=_auth(access_id, token), timeout=15)
        if not r.ok:
            if r.status_code == 401:
                return None, "Credenciais inválidas (401). Verifique Usuário API e Token."
            return None, f"Erro {r.status_code}: {r.text[:200]}"
        return r.json(), None
    except requests.ConnectionError as e:
        return None, f"Sem conexão com a API: {e}"
    except requests.Timeout:
        return None, "Timeout: a API demorou muito para responder."
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def fetch_business(sub, aid, tok):
    """Retorna (business_id, business_name, error)."""
    data, err = _get("/business/list", {"subscriber_id": sub}, aid, tok)
    if data and isinstance(data, list) and data:
        return str(data[0].get("id", "")), data[0].get("BusinessName", "Clínica"), None
    return "", "", err or "Nenhuma clínica encontrada."


def fetch_revenue(sub, aid, tok, bid, from_d, to_d):
    """Faturamento (receitas) do período via list_cash_flow."""
    data, err = _get(
        "/financial/list_cash_flow",
        {"subscriber_id": sub, "from": from_d.isoformat(),
         "to": to_d.isoformat(), "business_id": bid},
        aid, tok,
    )
    if data is None:
        return 0.0, err
    items = data if isinstance(data, list) else [data]
    return sum(float(x.get("in") or 0) for x in items), None


def fetch_appt_list(sub, aid, tok, bid, from_d, to_d):
    """Lista de agendamentos do período."""
    data, err = _get(
        "/appointment/list",
        {"subscriber_id": sub, "from": from_d.isoformat(),
         "to": to_d.isoformat(), "businessId": bid},
        aid, tok,
    )
    if not data or not isinstance(data, list):
        return [], err
    return [a for a in data if not a.get("Deleted")], None


def fetch_estimates(sub, aid, tok, bid, from_d, to_d):
    """Lista de orçamentos/avaliações do período."""
    data, err = _get(
        "/estimates/list",
        {"subscriber_id": sub, "from": from_d.isoformat(),
         "to": to_d.isoformat(), "business_id": bid},
        aid, tok,
    )
    return (data if isinstance(data, list) else []), err


@st.cache_data(ttl=300)
def fetch_full_agenda_raw(sub, aid, tok, bid, from_d, to_d):
    """Busca TODOS os agendamentos do mês incluindo cancelados (para análise de falta/cancelamento)."""
    url = f"{BASE_URL}/appointment/list"
    encoded = base64.b64encode(f"{sub}:{tok}".encode()).decode()
    headers = {"Authorization": f"Basic {encoded}"}
    try:
        r = requests.get(url, params={
            "subscriber_id": sub, "from": from_d.isoformat(),
            "to": to_d.isoformat(), "businessId": bid
        }, headers=headers, timeout=15)
        if not r.ok:
            return [], f"Erro {r.status_code}"
        data = r.json()
        return data if isinstance(data, list) else [], None
    except Exception as e:
        return [], str(e)


def fetch_daily_receipts(sub, aid, tok, bid, from_d, to_d):
    """Tenta obter receitas diárias acumuladas. Retorna {date: cumul} ou None."""
    data, _ = _get(
        "/financial/list_receipt",
        {"subscriber_id": sub, "from": from_d.isoformat(),
         "to": to_d.isoformat(), "business_id": bid},
        aid, tok,
    )
    if not data or not isinstance(data, list):
        return None
    df = pd.DataFrame(data)
    date_col = find_col(df, ("date", "data", "dt"))
    val_col = find_col(df, ("value", "amount", "total", "valor"))
    if not date_col or not val_col:
        return None
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    df[val_col] = pd.to_numeric(df[val_col], errors="coerce").fillna(0)
    return df.groupby(date_col)[val_col].sum().sort_index().cumsum().to_dict()


# ── SIDEBAR ─────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuração")

    try:
        _sec = st.secrets.get("clinicorp", {})
        _aid_secret = _sec.get("access_id", "")
        _tok_secret = _sec.get("token", "")
    except Exception:
        _aid_secret = _tok_secret = ""

    if _aid_secret and _tok_secret:
        aid = _aid_secret
        tok = _tok_secret
        sub = aid
        st.success("🔒 Credenciais carregadas automaticamente.")
    else:
        with st.expander("🔑 Credenciais Clinicorp", expanded=True):
            st.caption("Obtenha em: **Gerenciar Assinatura → Acesso Externo e Integrações**")
            aid = st.text_input("Usuário API", type="password")
            tok = st.text_input("Token API", type="password")
            sub = aid

    st.divider()

    today = date.today()
    st.subheader(f"Meta — {today.strftime('%B/%Y')}")
    meta = st.number_input(
        "Meta de faturamento (orçamentos aprovados)",
        min_value=0.0, value=220_000.0, step=1_000.0,
        format="%.2f", label_visibility="collapsed",
    )

    st.divider()

    if st.button("🔄 Atualizar dados", use_container_width=True, type="primary"):
        st.cache_data.clear()
        st.rerun()

    st.caption("Cache automático: 5 minutos")

    st.divider()
    debug_mode = st.toggle("🛠️ Modo debug", value=False)


# ── GATE ────────────────────────────────────────────────────────────────────────
if not (aid and tok):
    st.title("Dashboard da Clínica")
    st.info("👈 Insira suas credenciais do Clinicorp na barra lateral para começar.")
    with st.expander("Como obter as credenciais?"):
        st.markdown("""
1. Acesse **sistema.clinicorp.com**
2. Clique em **Gerenciar Assinatura**
3. Acesse **Acesso Externo e Integrações**
4. Copie o **Usuário API** e o **Token API**
        """)
    st.stop()


# ── BUSCAR DADOS BASE ────────────────────────────────────────────────────────────
today = date.today()
dim = calendar.monthrange(today.year, today.month)[1]
elapsed = today.day
remaining = dim - elapsed
first = date(today.year, today.month, 1)

with st.spinner("Conectando à API do Clinicorp..."):
    bid, bname, bid_err = fetch_business(sub, aid, tok)

if bid_err and not bid:
    st.error(f"**Erro:** {bid_err}")
    st.stop()

with st.spinner(f"Buscando dados de {bname}..."):
    recebido, rev_err   = fetch_revenue(sub, aid, tok, bid, first, today)
    appts_list, apt_err = fetch_appt_list(sub, aid, tok, bid, first, today)
    estimates_mes, _    = fetch_estimates(sub, aid, tok, bid, first, today)

err = rev_err or apt_err
if err:
    st.error(f"**Erro na API:** {err}")
    st.stop()

appts = len(appts_list)

# Faturamento = orçamentos aprovados no mês
fat_approved = [e for e in estimates_mes if e.get("Status") == "APPROVED"]
fat = sum(float(e.get("Amount") or 0) for e in fat_approved)
total_evals_mes = len(estimates_mes)
approved_evals_mes = len(fat_approved)

# Recebido no mês = caixa real recebido (do list_cash_flow)
recebido_fat = recebido

# A receber = aprovado - recebido (mínimo 0)
a_receber = max(0.0, fat - recebido_fat)

ticket = fat / approved_evals_mes if approved_evals_mes else 0.0
pct = (fat / meta * 100) if meta else 0.0
daily_avg = fat / elapsed if elapsed else 0.0
proj = fat + daily_avg * remaining
conv_rate_mes = (approved_evals_mes / total_evals_mes * 100) if total_evals_mes else 0.0

if debug_mode:
    with st.expander("🛠️ Debug", expanded=True):
        st.write(f"business_id: `{bid}` | business_name: `{bname}`")
        st.write(f"Faturamento (orç. aprovados): `{fat}` | Recebido (cash_flow): `{recebido}` | Atendimentos: `{appts}`")


# ── HEADER ──────────────────────────────────────────────────────────────────────
st.title(f"Dashboard — {bname}")
st.markdown(
    f"**{first.strftime('%d/%m')} → {today.strftime('%d/%m/%Y')}**"
    f"&nbsp;|&nbsp; Dia **{elapsed}** de **{dim}**"
    f"&nbsp;|&nbsp; **{remaining}** dias restantes"
)


# ── ABAS PRINCIPAIS ──────────────────────────────────────────────────────────────
page_overview, page_prof, page_agenda = st.tabs(["📊 Visão Geral", "👨‍⚕️ Profissionais", "📅 Agenda & Previsão"])


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 1 — VISÃO GERAL
# ═══════════════════════════════════════════════════════════════════════════════
with page_overview:

    # KPI Cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Orçamentos Aprovados", fmt_brl(fat))
    c2.metric("🏦 Recebido no Mês", fmt_brl(recebido_fat))
    c3.metric("🎯 Meta do Mês", fmt_brl(meta))
    c4.metric("👥 Atendimentos", f"{appts:,}")
    c5.metric("🎟️ Ticket Médio", fmt_brl(ticket))

    # Comparativo Aprovado x Recebido
    st.markdown("---")
    st.subheader("📊 Orçamentos Aprovados vs Recebido")

    col_comp, col_info = st.columns([3, 1])
    with col_comp:
        fig_comp = go.Figure()
        fig_comp.add_trace(go.Bar(
            name="Aprovado", x=["Mês atual"],
            y=[fat], marker_color="#2E86AB",
            text=[fmt_brl(fat)], textposition="outside",
        ))
        fig_comp.add_trace(go.Bar(
            name="Recebido", x=["Mês atual"],
            y=[recebido_fat], marker_color="#4CAF50",
            text=[fmt_brl(recebido_fat)], textposition="outside",
        ))
        fig_comp.add_trace(go.Bar(
            name="A Receber", x=["Mês atual"],
            y=[a_receber], marker_color="#FFB74D",
            text=[fmt_brl(a_receber)], textposition="outside",
        ))
        fig_comp.add_hline(y=meta, line_dash="dot", line_color="#FF6B6B",
                           annotation_text=f"Meta: {fmt_brl(meta)}")
        fig_comp.update_layout(
            height=320, barmode="group",
            margin=dict(l=0, r=0, t=30, b=0),
            yaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_comp, use_container_width=True)

    with col_info:
        receb_pct = (recebido_fat / fat * 100) if fat else 0
        st.metric("% Recebido", f"{receb_pct:.1f}%")
        st.metric("A Receber", fmt_brl(a_receber))
        st.metric("vs Meta", fmt_brl(fat - meta),
                  delta_color="normal" if fat >= meta else "inverse")

    # Barra de progresso
    st.markdown("---")
    col_bar, col_proj = st.columns([5, 1])
    with col_bar:
        icon = "🟢" if pct >= 100 else ("🟡" if pct >= 70 else "🔴")
        if fat >= meta:
            st.markdown(f"**{icon} Meta atingida! {pct:.1f}% — superou em {fmt_brl(fat - meta)}**")
        else:
            st.markdown(
                f"**{icon} Meta: {pct:.1f}% atingido**"
                f" — faltam **{fmt_brl(meta - fat)}** para bater"
            )
        st.progress(min(pct / 100, 1.0))
    with col_proj:
        st.metric("Projeção do Mês", fmt_brl(proj), help="Baseada na média diária atual")

    # ── INSIGHTS COMERCIAIS ───────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("💡 Insights Comerciais")

    receb_pct_ins = (recebido_fat / fat * 100) if fat else 0.0
    remaining_to_goal = max(0.0, meta - fat)
    approvals_needed = math.ceil(remaining_to_goal / ticket) if ticket > 0 and remaining_to_goal > 0 else 0
    evals_needed = math.ceil(approvals_needed / (conv_rate_mes / 100)) if conv_rate_mes > 0 and approvals_needed > 0 else 0
    daily_evals_needed = math.ceil(evals_needed / remaining) if remaining > 0 and evals_needed > 0 else 0
    daily_evals_pace = total_evals_mes / elapsed if elapsed > 0 else 0.0

    ins1, ins2, ins3 = st.columns(3)

    with ins1:
        if fat == 0:
            st.error("**🔴 Nenhum orçamento aprovado**  \nSem aprovações no mês ainda.")
        elif receb_pct_ins >= 80:
            st.success(
                f"**✅ {receb_pct_ins:.1f}% do aprovado já recebido**  \n"
                f"Caixa saudável! Faltam apenas {fmt_brl(fat - recebido_fat)}."
            )
        elif receb_pct_ins >= 50:
            st.warning(
                f"**⚠️ {receb_pct_ins:.1f}% do aprovado recebido**  \n"
                f"Ainda há {fmt_brl(fat - recebido_fat)} a receber dos aprovados."
            )
        else:
            st.error(
                f"**🔴 Só {receb_pct_ins:.1f}% do aprovado foi recebido**  \n"
                f"{fmt_brl(fat - recebido_fat)} ainda não entraram no caixa. Reforce a cobrança!"
            )

    with ins2:
        if total_evals_mes == 0:
            st.error("**🔴 Nenhuma avaliação no mês**  \nSem avaliações registradas. Isso é um gargalo crítico!")
        elif conv_rate_mes >= 70:
            st.success(
                f"**✅ Conversão: {conv_rate_mes:.1f}%**  \n"
                f"{approved_evals_mes} de {total_evals_mes} avaliações aprovadas. Ótimo!"
            )
        elif conv_rate_mes >= 50:
            st.warning(
                f"**⚠️ Conversão: {conv_rate_mes:.1f}%**  \n"
                f"{approved_evals_mes} de {total_evals_mes} avaliações aprovadas. Há espaço para melhorar."
            )
        else:
            st.error(
                f"**🔴 Conversão baixa: {conv_rate_mes:.1f}%**  \n"
                f"Só {approved_evals_mes} de {total_evals_mes} converteram. Revise o processo comercial!"
            )

    with ins3:
        if fat >= meta:
            st.success(
                f"**✅ Meta atingida!**  \n"
                f"Superou em {fmt_brl(fat - meta)}. Continue o ritmo!"
            )
        elif remaining <= 0:
            st.error(f"**🔴 Mês encerrado sem bater a meta**  \nFaltaram {fmt_brl(remaining_to_goal)}.")
        elif daily_evals_pace >= daily_evals_needed and daily_evals_needed > 0:
            st.success(
                f"**✅ Ritmo OK: {daily_evals_pace:.1f} aval./dia**  \n"
                f"No ritmo certo! Meta ao alcance."
            )
        elif daily_evals_needed > 0:
            st.error(
                f"**🔴 Ritmo insuficiente: {daily_evals_pace:.1f} aval./dia**  \n"
                f"Precisa de {daily_evals_needed} aval./dia para bater a meta."
            )
        else:
            st.info(
                f"**📋 Ritmo atual: {daily_evals_pace:.1f} aval./dia**  \n"
                f"Defina uma meta para calcular o ritmo necessário."
            )

    if remaining_to_goal > 0 and remaining > 0:
        st.markdown("#### 🎯 O que precisa acontecer para bater a meta?")
        mp1, mp2, mp3, mp4 = st.columns(4)
        mp1.metric("Falta para a meta", fmt_brl(remaining_to_goal))
        mp2.metric("Aprovações necessárias", str(approvals_needed),
                   help=f"Com ticket médio de {fmt_brl(ticket)}")
        mp3.metric("Avaliações necessárias", str(evals_needed),
                   help=f"Com taxa de conversão de {conv_rate_mes:.1f}%")
        mp4.metric("Avaliações por dia", str(daily_evals_needed),
                   help=f"Para os {remaining} dias restantes do mês")
        if evals_needed > 0:
            st.info(
                f"Com ticket médio de **{fmt_brl(ticket)}** e conversão de **{conv_rate_mes:.1f}%**, "
                f"você precisa de **{evals_needed} avaliações** nos próximos **{remaining} dias** "
                f"(~{daily_evals_needed}/dia) para atingir a meta de **{fmt_brl(meta)}**."
            )
    elif fat >= meta:
        st.success(f"🎉 Meta atingida! Você superou em **{fmt_brl(fat - meta)}**.")

    # Evolução diária
    st.markdown("---")
    st.subheader("📈 Evolução Diária do Faturamento")

    # Evolução diária com base nos orçamentos aprovados por data
    all_days = pd.date_range(first, today, freq="D")
    n_days = len(all_days)
    daily_note = ""

    if fat_approved:
        df_fat_daily = pd.DataFrame(fat_approved)
        df_fat_daily["_date"] = pd.to_datetime(
            df_fat_daily.get("SearchDate", df_fat_daily.get("Date")), errors="coerce"
        ).dt.date
        df_fat_daily["Amount"] = pd.to_numeric(df_fat_daily["Amount"], errors="coerce").fillna(0)
        daily_sum = df_fat_daily.groupby("_date")["Amount"].sum()
        cumulative = 0.0
        actuals = []
        for d in all_days:
            cumulative += float(daily_sum.get(d.date(), 0))
            actuals.append(cumulative)
    else:
        actuals = [fat * (i / n_days) for i in range(1, n_days + 1)]
        daily_note = "Evolução estimada linearmente com base no total aprovado."

    full_month = pd.date_range(first, date(today.year, today.month, dim), freq="D")
    targets = [(meta / dim) * i for i in range(1, dim + 1)]

    fig_daily = go.Figure()
    fig_daily.add_trace(go.Scatter(
        x=[d.date() for d in full_month], y=targets,
        name="Meta Acumulada", mode="lines",
        line=dict(color="#FF6B6B", width=2, dash="dash"),
    ))
    fig_daily.add_trace(go.Scatter(
        x=[d.date() for d in all_days], y=actuals,
        name="Faturamento Real", mode="lines+markers",
        line=dict(color="#2E86AB", width=2.5),
        marker=dict(size=5),
        fill="tozeroy", fillcolor="rgba(46,134,171,0.1)",
    ))
    fig_daily.update_layout(
        height=380, margin=dict(l=0, r=0, t=10, b=0),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.1, x=0),
        yaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
        xaxis=dict(tickformat="%d/%m"),
    )
    st.plotly_chart(fig_daily, use_container_width=True)
    if daily_note:
        st.caption(f"ℹ️ {daily_note}")

    # Comparativo mensal
    st.markdown("---")
    st.subheader("📊 Comparativo — Últimos 6 Meses")

    rows = []
    with st.spinner("Carregando histórico dos últimos 6 meses..."):
        for i in range(5, 0, -1):
            s, e = nth_month_back(today, i)
            ests, _ = fetch_estimates(sub, aid, tok, bid, s, e)
            al, _   = fetch_appt_list(sub, aid, tok, bid, s, e)
            fat_hist = sum(float(x.get("Amount") or 0) for x in ests if x.get("Status") == "APPROVED")
            rec_hist, _ = fetch_revenue(sub, aid, tok, bid, s, e)
            a = len(al)
            rows.append({"Mês": s.strftime("%b/%y"), "Faturamento": fat_hist,
                         "Recebido": rec_hist, "Atendimentos": a,
                         "Ticket": fat_hist / len([x for x in ests if x.get("Status") == "APPROVED"]) if any(x.get("Status") == "APPROVED" for x in ests) else 0.0,
                         "atual": False})
        rows.append({"Mês": today.strftime("%b/%y"), "Faturamento": fat,
                     "Recebido": recebido, "Atendimentos": appts,
                     "Ticket": ticket, "atual": True})

    df_m = pd.DataFrame(rows)
    bar_colors = ["#FF6B6B" if r["atual"] else "#2E86AB" for _, r in df_m.iterrows()]

    tab_f, tab_a, tab_t = st.tabs(["💰 Faturamento", "👥 Atendimentos", "🎟️ Ticket Médio"])

    with tab_f:
        bf = go.Figure()
        bf.add_trace(go.Bar(
            name="Aprovado", x=df_m["Mês"], y=df_m["Faturamento"],
            marker_color=bar_colors,
            text=[fmt_brl(v) for v in df_m["Faturamento"]], textposition="outside",
        ))
        bf.add_trace(go.Bar(
            name="Recebido", x=df_m["Mês"], y=df_m["Recebido"],
            marker_color=["#FF6B6B" if r["atual"] else "#94C9E8" for _, r in df_m.iterrows()],
            text=[fmt_brl(v) for v in df_m["Recebido"]], textposition="outside",
        ))
        bf.add_hline(y=meta, line_dash="dot", line_color="#FF6B6B",
                     annotation_text=f"Meta: {fmt_brl(meta)}", annotation_position="top left")
        bf.update_layout(height=320, margin=dict(l=0, r=0, t=30, b=0),
                         barmode="group",
                         yaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
                         legend=dict(orientation="h", y=1.1))
        st.plotly_chart(bf, use_container_width=True)

    with tab_a:
        ba = go.Figure(go.Bar(
            x=df_m["Mês"], y=df_m["Atendimentos"], marker_color=bar_colors,
            text=df_m["Atendimentos"], textposition="outside",
        ))
        ba.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(ba, use_container_width=True)

    with tab_t:
        bt = go.Figure(go.Bar(
            x=df_m["Mês"], y=df_m["Ticket"], marker_color=bar_colors,
            text=[fmt_brl(v) for v in df_m["Ticket"]], textposition="outside",
        ))
        bt.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0),
                         yaxis=dict(tickprefix="R$ ", tickformat=",.0f"))
        st.plotly_chart(bt, use_container_width=True)

    st.markdown("---")
    st.caption(
        f"Média diária: **{fmt_brl(daily_avg)}** &nbsp;|&nbsp; "
        f"Projeção para o fim do mês: **{fmt_brl(proj)}** &nbsp;|&nbsp; "
        f"Dados via API Clinicorp"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 2 — PROFISSIONAIS
# ═══════════════════════════════════════════════════════════════════════════════
with page_prof:

    with st.spinner("Buscando avaliações e dados por profissional..."):
        estimates, _ = fetch_estimates(sub, aid, tok, bid, first, today)

    # ── Avaliações do Mês ──────────────────────────────────────────────────────
    st.subheader("🔍 Avaliações do Mês")

    total_evals = len(estimates)
    approved_evals = len([e for e in estimates if e.get("Status") == "APPROVED"])
    approved_amount = sum(float(e.get("Amount") or 0) for e in estimates if e.get("Status") == "APPROVED")
    total_amount = sum(float(e.get("Amount") or 0) for e in estimates)
    conv_rate = (approved_evals / total_evals * 100) if total_evals else 0.0
    pending = total_evals - approved_evals

    ea1, ea2, ea3, ea4 = st.columns(4)
    ea1.metric("📋 Avaliações Feitas", f"{total_evals:,}")
    ea2.metric("✅ Aprovadas", f"{approved_evals:,}")
    ea3.metric("📈 Taxa de Conversão", f"{conv_rate:.1f}%")
    ea4.metric("💵 Valor Aprovado", fmt_brl(approved_amount))

    if total_evals > 0:
        fig_funil = go.Figure(go.Funnel(
            y=["Avaliações Feitas", "Aprovadas"],
            x=[total_evals, approved_evals],
            textinfo="value+percent initial",
            marker=dict(color=["#2E86AB", "#4CAF50"]),
        ))
        fig_funil.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_funil, use_container_width=True)
        if pending > 0:
            st.caption(
                f"ℹ️ {pending} avaliação(ões) pendente(s) — "
                f"valor em aberto: {fmt_brl(total_amount - approved_amount)}"
            )

    # ── Desempenho por Profissional ────────────────────────────────────────────
    st.markdown("---")
    st.subheader("👨‍⚕️ Desempenho por Profissional")

    if estimates:
        df_est = pd.DataFrame(estimates)

        if "ProfessionalName" in df_est.columns and "Amount" in df_est.columns:
            df_est["Amount"] = pd.to_numeric(df_est["Amount"], errors="coerce").fillna(0)
            df_est["ProfessionalName"] = df_est["ProfessionalName"].fillna("(sem nome)")

            df_prof = df_est.groupby("ProfessionalName", as_index=False).agg(
                Avaliações=("id", "count"),
                Valor_Total=("Amount", "sum"),
            )
            df_prof["Ticket Médio"] = df_prof["Valor_Total"] / df_prof["Avaliações"]

            tp1, tp2, tp3 = st.tabs(["💰 Valor por Profissional", "🎟️ Ticket Médio", "📋 Avaliações"])

            with tp1:
                df_r = df_prof.sort_values("Valor_Total", ascending=True)
                fig_r = go.Figure(go.Bar(
                    x=df_r["Valor_Total"], y=df_r["ProfessionalName"],
                    orientation="h", marker_color="#2E86AB",
                    text=[fmt_brl(v) for v in df_r["Valor_Total"]],
                    textposition="outside",
                ))
                fig_r.update_layout(
                    height=max(300, len(df_r) * 55),
                    margin=dict(l=0, r=120, t=10, b=0),
                    xaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
                )
                st.plotly_chart(fig_r, use_container_width=True)
                st.caption("Valor baseado nos orçamentos emitidos no período.")

            with tp2:
                df_t = df_prof.sort_values("Ticket Médio", ascending=True)
                fig_t = go.Figure(go.Bar(
                    x=df_t["Ticket Médio"], y=df_t["ProfessionalName"],
                    orientation="h", marker_color="#4ECDC4",
                    text=[fmt_brl(v) for v in df_t["Ticket Médio"]],
                    textposition="outside",
                ))
                fig_t.update_layout(
                    height=max(300, len(df_t) * 55),
                    margin=dict(l=0, r=120, t=10, b=0),
                    xaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
                )
                st.plotly_chart(fig_t, use_container_width=True)

            with tp3:
                df_ev = df_prof.sort_values("Avaliações", ascending=True)
                fig_ev = go.Figure(go.Bar(
                    x=df_ev["Avaliações"], y=df_ev["ProfessionalName"],
                    orientation="h", marker_color="#FF6B6B",
                    text=df_ev["Avaliações"], textposition="outside",
                ))
                fig_ev.update_layout(
                    height=max(300, len(df_ev) * 55),
                    margin=dict(l=0, r=60, t=10, b=0),
                )
                st.plotly_chart(fig_ev, use_container_width=True)
        else:
            st.info("Os orçamentos não retornaram dados de profissional.")
    else:
        st.info("Nenhuma avaliação encontrada neste período.")


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 3 — AGENDA & PREVISÃO
# ═══════════════════════════════════════════════════════════════════════════════
with page_agenda:
    st.title(f"📅 Agenda — {today.strftime('%B/%Y')}")

    with st.spinner("Carregando agenda completa do mês..."):
        last_day = date(today.year, today.month, dim)
        all_appts_raw, agenda_err = fetch_full_agenda_raw(sub, aid, tok, bid, first, last_day)

    if agenda_err and not all_appts_raw:
        st.error(f"Erro ao carregar agenda: {agenda_err}")
    else:
        today_atomic = int(today.strftime("%Y%m%d"))

        active_appts  = [a for a in all_appts_raw if not a.get("Deleted")]
        cancelled_appts = [a for a in all_appts_raw if a.get("Deleted")]

        past_active   = [a for a in active_appts if a.get("AtomicDate", 0) <= today_atomic]
        future_active = [a for a in active_appts if a.get("AtomicDate", 0) > today_atomic]

        new_patients  = [a for a in active_appts if a.get("isNew") not in ("", None, False, 0)]
        ret_patients  = [a for a in active_appts if a.get("isNew") in ("", None, False, 0)]

        past_pace     = len(past_active) / elapsed if elapsed else 0
        future_pace   = len(future_active) / remaining if remaining else 0
        cancel_rate   = (len(cancelled_appts) / len(all_appts_raw) * 100) if all_appts_raw else 0
        new_pct       = (len(new_patients) / len(active_appts) * 100) if active_appts else 0

        # ── KPIs ──────────────────────────────────────────────────────────────
        ag1, ag2, ag3, ag4, ag5 = st.columns(5)
        ag1.metric("📋 Total no mês",         len(active_appts))
        ag2.metric(f"✅ Realizados (1–{elapsed})", len(past_active),
                   help=f"Média: {past_pace:.1f}/dia")
        ag3.metric(f"📅 Agendados ({elapsed+1}–{dim})", len(future_active),
                   help=f"Média agendada: {future_pace:.1f}/dia")
        ag4.metric("❌ Cancelamentos",         len(cancelled_appts),
                   help=f"{cancel_rate:.1f}% do total de agendamentos do mês")
        ag5.metric("🆕 Pacientes Novos",       len(new_patients),
                   help=f"{new_pct:.1f}% dos atendimentos ativos")

        # ── Alertas ───────────────────────────────────────────────────────────
        st.markdown("---")
        pal1, pal2, pal3 = st.columns(3)

        with pal1:
            if remaining > 0:
                pace_drop_pct = ((past_pace - future_pace) / past_pace * 100) if past_pace > 0 else 0
                if pace_drop_pct > 30:
                    st.error(
                        f"**🔴 2ª quinzena {pace_drop_pct:.0f}% mais vazia**  \n"
                        f"Ritmo atual: **{past_pace:.1f}/dia** → Agendado: **{future_pace:.1f}/dia**.  \n"
                        f"Preencha os horários urgente!"
                    )
                elif pace_drop_pct > 15:
                    st.warning(
                        f"**⚠️ Queda de ritmo: {pace_drop_pct:.0f}%**  \n"
                        f"De {past_pace:.1f}/dia para {future_pace:.1f}/dia na 2ª quinzena."
                    )
                else:
                    st.success(
                        f"**✅ Agenda equilibrada**  \n"
                        f"{past_pace:.1f}/dia realizados → {future_pace:.1f}/dia agendados."
                    )
            else:
                st.info("Mês encerrado.")

        with pal2:
            if cancel_rate >= 20:
                st.error(
                    f"**🔴 Cancelamentos altos: {cancel_rate:.1f}%**  \n"
                    f"{len(cancelled_appts)} cancelados no mês.  \n"
                    f"Revise o processo de confirmação de consultas!"
                )
            elif cancel_rate >= 10:
                st.warning(
                    f"**⚠️ Cancelamentos: {cancel_rate:.1f}%**  \n"
                    f"{len(cancelled_appts)} cancelados. Avalie envio de lembretes automáticos."
                )
            else:
                st.success(
                    f"**✅ Cancelamentos sob controle: {cancel_rate:.1f}%**  \n"
                    f"Apenas {len(cancelled_appts)} cancelados no mês."
                )

        with pal3:
            if new_pct >= 20:
                st.success(
                    f"**✅ Captação saudável: {new_pct:.1f}% novos**  \n"
                    f"{len(new_patients)} novos pacientes no mês. Funil ativo!"
                )
            elif new_pct >= 10:
                st.warning(
                    f"**⚠️ Poucos pacientes novos: {new_pct:.1f}%**  \n"
                    f"{len(new_patients)} novos no mês. Invista em captação."
                )
            else:
                st.error(
                    f"**🔴 Captação crítica: {new_pct:.1f}% novos**  \n"
                    f"Quase sem novos pacientes — a base atual não sustenta crescimento!"
                )

        # ── Gráfico: agendamentos por dia ──────────────────────────────────────
        st.markdown("---")
        st.subheader("📊 Agendamentos por dia")

        df_ag = pd.DataFrame(active_appts)
        if not df_ag.empty and "AtomicDate" in df_ag.columns:
            df_ag["data"] = pd.to_datetime(df_ag["AtomicDate"].astype(str), format="%Y%m%d").dt.date
            df_ag["status"] = df_ag["AtomicDate"].apply(
                lambda x: "Realizado" if x <= today_atomic else "Agendado"
            )
            dc = df_ag.groupby(["data", "status"]).size().reset_index(name="qtd")

            fig_ag = go.Figure()
            for status, color in [("Realizado", "#2E86AB"), ("Agendado", "#94C9E8")]:
                ds = dc[dc["status"] == status]
                fig_ag.add_trace(go.Bar(
                    x=ds["data"], y=ds["qtd"], name=status,
                    marker_color=color, text=ds["qtd"], textposition="outside",
                ))
            fig_ag.update_layout(
                height=300, barmode="group",
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(tickformat="%d/%m"),
                legend=dict(orientation="h", y=1.1),
            )
            st.plotly_chart(fig_ag, use_container_width=True)

        # ── Dias úteis com agenda fraca ────────────────────────────────────────
        if remaining > 0:
            weekday_pt = {0: "Seg", 1: "Ter", 2: "Qua", 3: "Qui", 4: "Sex"}
            future_by_day = {}
            d_iter = today + timedelta(days=1)
            while d_iter <= last_day:
                if d_iter.weekday() < 5:
                    atomic = int(d_iter.strftime("%Y%m%d"))
                    cnt = sum(1 for a in future_active if a.get("AtomicDate") == atomic)
                    future_by_day[d_iter] = cnt
                d_iter += timedelta(days=1)

            light_days = [(d, c) for d, c in sorted(future_by_day.items()) if c < 5]
            if light_days:
                st.markdown("#### ⚠️ Dias úteis com agenda fraca (menos de 5 agendamentos)")
                cols_ld = st.columns(min(len(light_days), 6))
                for i, (d, c) in enumerate(light_days[:6]):
                    label = f"{d.strftime('%d/%m')} ({weekday_pt.get(d.weekday(), '')})"
                    cols_ld[i].metric(label, f"{c} agend." if c > 0 else "🔴 Vazio")

        # ── Projeção de receita pela agenda ────────────────────────────────────
        st.markdown("---")
        st.subheader("💰 Projeção de Receita pela Agenda")

        avg_rev_per_appt = fat / len(past_active) if past_active else 0
        proj_agenda = fat + (avg_rev_per_appt * len(future_active))

        pr1, pr2, pr3, pr4 = st.columns(4)
        pr1.metric("Receita atual (aprovado)",  fmt_brl(fat))
        pr2.metric("Receita média por atend.",  fmt_brl(avg_rev_per_appt),
                   help="Orçamentos aprovados ÷ atendimentos realizados no mês")
        pr3.metric("Agendamentos restantes",    len(future_active))
        pr4.metric("Projeção pela agenda",      fmt_brl(proj_agenda),
                   delta=fmt_brl(proj_agenda - meta),
                   delta_color="normal" if proj_agenda >= meta else "inverse")

        if proj_agenda >= meta:
            st.success(
                f"✅ Se os **{len(future_active)} agendamentos restantes** se mantiverem, "
                f"a projeção é de **{fmt_brl(proj_agenda)}** — superando a meta em **{fmt_brl(proj_agenda - meta)}**."
            )
        else:
            appts_faltam = math.ceil((meta - proj_agenda) / avg_rev_per_appt) if avg_rev_per_appt > 0 else 0
            st.error(
                f"🔴 Com a agenda atual, a projeção é **{fmt_brl(proj_agenda)}** — "
                f"**{fmt_brl(meta - proj_agenda)}** abaixo da meta.  \n"
                f"São necessários mais **{appts_faltam} atendimentos** para fechar a meta."
            )

        st.caption(
            "⚠️ Projeção baseada na receita média por atendimento do mês atual. "
            "Nem todo agendamento gera orçamento aprovado — use como referência, não como certeza."
        )

        # ── Por procedimento + Novos vs Retorno ───────────────────────────────
        st.markdown("---")
        col_cat2, col_np = st.columns(2)

        with col_cat2:
            st.markdown("#### Por procedimento (mês todo)")
            if not df_ag.empty and "CategoryDescription" in df_ag.columns:
                df_cat = df_ag.copy()
                df_cat["cat"] = df_cat["CategoryDescription"].str.strip().replace("", "Sem categoria")
                cats_df = df_cat[df_cat["cat"] != ""].groupby("cat").size().reset_index(name="qtd")
                cats_df = cats_df.sort_values("qtd", ascending=True)
                fig_cat2 = go.Figure(go.Bar(
                    x=cats_df["qtd"], y=cats_df["cat"],
                    orientation="h", marker_color="#4ECDC4",
                    text=cats_df["qtd"], textposition="outside",
                ))
                fig_cat2.update_layout(
                    height=max(300, len(cats_df) * 28),
                    margin=dict(l=0, r=60, t=10, b=0),
                )
                st.plotly_chart(fig_cat2, use_container_width=True)

        with col_np:
            st.markdown("#### Novos pacientes vs Retorno")
            if new_patients or ret_patients:
                fig_np = go.Figure(go.Pie(
                    labels=["Novos", "Retorno"],
                    values=[len(new_patients), len(ret_patients)],
                    marker=dict(colors=["#FF6B6B", "#2E86AB"]),
                    hole=0.45,
                    textinfo="label+percent+value",
                ))
                fig_np.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig_np, use_container_width=True)
                st.caption(
                    "Baseado no campo 'isNew' do Clinicorp. "
                    "Verifique se o sistema está registrando corretamente os primeiros atendimentos."
                )
