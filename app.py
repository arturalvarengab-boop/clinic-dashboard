import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import date
import base64
import calendar

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

# Recebido dos orçamentos aprovados (PaymentAccounted = "X" nas procedures)
recebido_fat = 0.0
for e in fat_approved:
    for proc in e.get("ProcedureList", []):
        if proc.get("PaymentAccounted") == "X":
            recebido_fat += float(proc.get("FinalAmount") or proc.get("Amount") or 0)

# A receber = aprovado - recebido
a_receber = fat - recebido_fat

ticket = fat / approved_evals_mes if approved_evals_mes else 0.0
pct = (fat / meta * 100) if meta else 0.0
daily_avg = fat / elapsed if elapsed else 0.0
proj = fat + daily_avg * remaining

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
page_overview, page_prof = st.tabs(["📊 Visão Geral", "👨‍⚕️ Profissionais"])


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 1 — VISÃO GERAL
# ═══════════════════════════════════════════════════════════════════════════════
with page_overview:

    # KPI Cards
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Orçamentos Aprovados", fmt_brl(fat))
    c2.metric("🏦 Recebido dos Orçamentos", fmt_brl(recebido_fat))
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
        if rev >= meta:
            st.markdown(f"**{icon} Meta atingida! {pct:.1f}% — superou em {fmt_brl(rev - meta)}**")
        else:
            st.markdown(
                f"**{icon} Meta: {pct:.1f}% atingido**"
                f" — faltam **{fmt_brl(meta - rev)}** para bater"
            )
        st.progress(min(pct / 100, 1.0))
    with col_proj:
        st.metric("Projeção do Mês", fmt_brl(proj), help="Baseada na média diária atual")

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
