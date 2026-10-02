import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta
import base64
import calendar
import json
import os
from pathlib import Path

# ── PAGE CONFIG ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Dashboard Clínica São Miguel",
    page_icon="🏥",
    layout="wide",
)

BASE_URL = "https://api.clinicorp.com/rest/v1"
PRICING_FILE = Path(__file__).parent / "pricing.json"


# ── UTILS ──────────────────────────────────────────────────────────────────────
def fmt_brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(v: float) -> str:
    return f"{v:.1f}%"


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


def delta_color(current, previous):
    """Returns delta string for st.metric."""
    if previous == 0:
        return None
    diff = current - previous
    return f"{'+' if diff >= 0 else ''}{fmt_brl(diff)}"


def delta_pct(current, previous):
    if previous == 0:
        return None
    diff = current - previous
    return f"{'+' if diff >= 0 else ''}{diff:.1f}%"


def delta_int(current, previous):
    if previous == 0:
        return None
    diff = current - previous
    return f"{'+' if diff >= 0 else ''}{int(diff)}"


# ── PRICING PERSISTENCE ────────────────────────────────────────────────────────
def load_pricing() -> list:
    if PRICING_FILE.exists():
        try:
            with open(PRICING_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("procedures", []), data.get("history", [])
        except Exception:
            pass
    default_procedures = [
        {"Procedimento": "Consulta / Avaliação", "Preço Atual": 150.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Limpeza (Profilaxia)", "Preço Atual": 220.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Restauração (resina)", "Preço Atual": 350.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Extração simples", "Preço Atual": 280.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Clareamento dental", "Preço Atual": 1200.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Ortodontia (mensalidade)", "Preço Atual": 450.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Canal (endodontia)", "Preço Atual": 900.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
        {"Procedimento": "Implante dentário", "Preço Atual": 3500.0,
         "Última Atualização": "2025-01-01", "Próxima Atualização": "2025-07-01", "Notas": ""},
    ]
    return default_procedures, []


def save_pricing(procedures: list, history: list):
    with open(PRICING_FILE, "w", encoding="utf-8") as f:
        json.dump({"procedures": procedures, "history": history}, f,
                  ensure_ascii=False, indent=2)


# ── CLASSIFY APPOINTMENTS ──────────────────────────────────────────────────────
def classify_appts(appts_list: list) -> dict:
    """
    Percorre appointments e detecta campos com múltiplos nomes possíveis.
    Retorna dict com: absences, first_consults, unique_patients, raw_statuses, field_names
    """
    absences = 0
    first_consults = 0
    unique_patients = set()
    first_consult_patient_ids = set()
    raw_statuses = set()
    raw_types = set()
    field_names_found = {}

    # Palavras que indicam falta / não comparecimento / cancelamento pelo paciente
    ABSENT_KEYWORDS = {
        "absent", "faltou", "missed", "no_show", "ausente", "falta",
        "nao_compareceu", "nao_confirmou", "naorealizou", "nao_realizado",
        "desmarcou", "remarcou", "adiou", "nao_atendido", "paciente_faltou",
        "nao_veio", "nao_apareceu", "nf", "f",
    }
    FIRST_CONSULT_KEYWORDS = {
        "primeira", "first", "avaliacao", "novo", "inicial", "avaliação",
        "new_patient", "primeiro_atendimento", "consulta_inicial", "prime",
        "1a_consulta", "1consulta", "new", "triagem", "acolhimento",
    }
    # Campos candidatos a ID de paciente
    PATIENT_ID_FIELDS = (
        "PatientId", "clientId", "PacienteId", "patient_id", "patientId",
        "ClientId", "paciente_id", "idPaciente", "idCliente", "id_paciente",
        "id_cliente", "IdPaciente", "IdCliente", "pacienteId",
    )
    # Campos candidatos a status
    STATUS_FIELDS = (
        "Status", "status", "StatusNome", "AppointmentStatus", "situacao",
        "Situacao", "StatusName", "statusName", "appointment_status",
        "StatusConsulta", "SituacaoConsulta", "estado", "Estado",
    )
    # Campos candidatos a tipo
    TYPE_FIELDS = (
        "Type", "type", "AppointmentType", "TipoConsulta", "tipo",
        "ConsultType", "TypeName", "typeName", "ProcedureType",
        "TipoAgendamento", "tipoConsulta", "appointment_type",
    )

    for appt in appts_list:
        # ── Detectar paciente ──────────────────────────────────────────────────
        for k in PATIENT_ID_FIELDS:
            if k in appt and appt[k]:
                unique_patients.add(str(appt[k]))
                field_names_found["patient_field"] = k
                break
        else:
            # Fallback: procurar qualquer campo terminando em "id" com valor numérico
            for k, v in appt.items():
                kl = k.lower()
                if ("paciente" in kl or "client" in kl or "patient" in kl) and v:
                    unique_patients.add(f"{k}:{v}")
                    field_names_found.setdefault("patient_field_fallback", k)
                    break

        # ── Detectar status / falta ────────────────────────────────────────────
        status_val = None
        for k in STATUS_FIELDS:
            if k in appt and appt[k] is not None:
                status_val = str(appt[k]).strip().upper().replace(" ", "_")
                field_names_found["status_field"] = k
                break
        else:
            # Fallback: procurar qualquer campo com "status" ou "situacao" no nome
            for k, v in appt.items():
                if ("status" in k.lower() or "situac" in k.lower()) and v:
                    status_val = str(v).strip().upper().replace(" ", "_")
                    field_names_found.setdefault("status_field_fallback", k)
                    break

        if status_val:
            raw_statuses.add(status_val)
            if any(kw in status_val for kw in ABSENT_KEYWORDS):
                absences += 1

        # ── Detectar tipo / primeira consulta ──────────────────────────────────
        type_val = None
        for k in TYPE_FIELDS:
            if k in appt and appt[k] is not None:
                type_val = str(appt[k]).strip().upper().replace(" ", "_")
                field_names_found["type_field"] = k
                break
        else:
            for k, v in appt.items():
                if ("tipo" in k.lower() or "type" in k.lower()) and v:
                    type_val = str(v).strip().upper().replace(" ", "_")
                    field_names_found.setdefault("type_field_fallback", k)
                    break

        if type_val:
            raw_types.add(type_val)
            if any(kw in type_val for kw in FIRST_CONSULT_KEYWORDS):
                first_consults += 1
                # Rastreia ID do paciente desta primeira consulta
                for k in PATIENT_ID_FIELDS:
                    if k in appt and appt[k]:
                        first_consult_patient_ids.add(str(appt[k]))
                        break
                else:
                    for k, v in appt.items():
                        if ("paciente" in k.lower() or "client" in k.lower() or "patient" in k.lower()) and v:
                            first_consult_patient_ids.add(f"{k}:{v}")
                            break

    return {
        "absences": absences,
        "first_consults": first_consults,
        "unique_patients": len(unique_patients),
        "first_consult_patient_ids": first_consult_patient_ids,
        "raw_statuses": raw_statuses,
        "raw_types": raw_types,
        "field_names_found": field_names_found,
    }


def count_patients_from_estimates(estimates_list: list) -> int:
    """Conta pacientes únicos a partir dos orçamentos (fallback confiável)."""
    ids = set()
    FIELDS = (
        "PatientId", "clientId", "PacienteId", "patient_id", "patientId",
        "ClientId", "paciente_id", "idPaciente", "idCliente", "pacienteId",
    )
    for e in estimates_list:
        for k in FIELDS:
            if k in e and e[k]:
                ids.add(str(e[k]))
                break
        else:
            # Fallback: qualquer campo com "paciente" ou "client" no nome
            for k, v in e.items():
                if ("paciente" in k.lower() or "client" in k.lower()) and v:
                    ids.add(f"{k}:{v}")
                    break
    return len(ids)


def analyze_followup(appts_list: list, first_consult_ids: set) -> dict:
    """
    Cruza pacientes de primeira consulta com o restante dos agendamentos.
    Retorna quantos tiveram follow-up (2ª consulta agendada/realizada).
    """
    if not first_consult_ids:
        return {"available": False, "reason": "Primeiras consultas não identificadas (campo de tipo não encontrado)"}

    PATIENT_ID_FIELDS = (
        "PatientId", "clientId", "PacienteId", "patient_id", "patientId",
        "ClientId", "paciente_id", "idPaciente", "idCliente", "pacienteId",
    )

    # Conta quantos agendamentos cada paciente teve no período
    patient_appt_count: dict = {}
    for appt in appts_list:
        pid = None
        for k in PATIENT_ID_FIELDS:
            if k in appt and appt[k]:
                pid = str(appt[k])
                break
        else:
            for k, v in appt.items():
                if ("paciente" in k.lower() or "client" in k.lower() or "patient" in k.lower()) and v:
                    pid = f"{k}:{v}"
                    break
        if pid:
            patient_appt_count[pid] = patient_appt_count.get(pid, 0) + 1

    # Pacientes de 1ª consulta que também têm outro agendamento = fizeram follow-up
    with_followup    = {pid for pid in first_consult_ids if patient_appt_count.get(pid, 0) > 1}
    without_followup = first_consult_ids - with_followup
    followup_rate    = (len(with_followup) / len(first_consult_ids) * 100) if first_consult_ids else 0.0

    return {
        "available": True,
        "total_first_consult": len(first_consult_ids),
        "with_followup": len(with_followup),
        "without_followup": len(without_followup),
        "followup_rate": followup_rate,
        "without_followup_ids": without_followup,   # para tabela futura
    }


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
    data, err = _get("/business/list", {"subscriber_id": sub}, aid, tok)
    if data and isinstance(data, list) and data:
        return str(data[0].get("id", "")), data[0].get("BusinessName", "Clínica"), None
    return "", "", err or "Nenhuma clínica encontrada."


def fetch_revenue(sub, aid, tok, bid, from_d, to_d):
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
    data, err = _get(
        "/estimates/list",
        {"subscriber_id": sub, "from": from_d.isoformat(),
         "to": to_d.isoformat(), "business_id": bid},
        aid, tok,
    )
    return (data if isinstance(data, list) else []), err


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

    # ── Seletor de Período ──────────────────────────────────────────────────────
    st.subheader("📅 Período")
    today = date.today()

    period_type = st.selectbox(
        "Selecionar período",
        ["Mês atual", "Mês anterior", "Últimos 7 dias", "Últimos 30 dias",
         "Últimos 90 dias", "Personalizado"],
        label_visibility="collapsed",
    )

    if period_type == "Mês atual":
        from_d = date(today.year, today.month, 1)
        to_d = today
    elif period_type == "Mês anterior":
        from_d, to_d = nth_month_back(today, 1)
    elif period_type == "Últimos 7 dias":
        from_d = today - timedelta(days=6)
        to_d = today
    elif period_type == "Últimos 30 dias":
        from_d = today - timedelta(days=29)
        to_d = today
    elif period_type == "Últimos 90 dias":
        from_d = today - timedelta(days=89)
        to_d = today
    else:
        from_d = st.date_input("De", value=date(today.year, today.month, 1))
        to_d = st.date_input("Até", value=today)

    # Período anterior para deltas
    n_period_days = (to_d - from_d).days + 1
    prev_to = from_d - timedelta(days=1)
    prev_from = prev_to - timedelta(days=n_period_days - 1)

    st.divider()

    st.subheader("🎯 Meta do Período")
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
    st.title("Dashboard Clínica São Miguel")
    st.info("👈 Insira suas credenciais do Clinicorp na barra lateral para começar.")
    with st.expander("Como obter as credenciais?"):
        st.markdown("""
1. Acesse **sistema.clinicorp.com**
2. Clique em **Gerenciar Assinatura**
3. Acesse **Acesso Externo e Integrações**
4. Copie o **Usuário API** e o **Token API**
        """)
    st.stop()


# ── BUSCAR DADOS ──────────────────────────────────────────────────────────────
with st.spinner("Conectando à API do Clinicorp..."):
    bid, bname, bid_err = fetch_business(sub, aid, tok)

if bid_err and not bid:
    st.error(f"**Erro:** {bid_err}")
    st.stop()

with st.spinner(f"Buscando dados de {bname} ({from_d.strftime('%d/%m')} → {to_d.strftime('%d/%m/%Y')})..."):
    recebido, rev_err   = fetch_revenue(sub, aid, tok, bid, from_d, to_d)
    appts_list, apt_err = fetch_appt_list(sub, aid, tok, bid, from_d, to_d)
    estimates_list, _   = fetch_estimates(sub, aid, tok, bid, from_d, to_d)

    # Período anterior para comparação
    recebido_prev, _     = fetch_revenue(sub, aid, tok, bid, prev_from, prev_to)
    appts_prev, _        = fetch_appt_list(sub, aid, tok, bid, prev_from, prev_to)
    estimates_prev, _    = fetch_estimates(sub, aid, tok, bid, prev_from, prev_to)

err = rev_err or apt_err
if err:
    st.error(f"**Erro na API:** {err}")
    st.stop()

# ── CALCULAR KPIs PERÍODO ATUAL ────────────────────────────────────────────────
appts = len(appts_list)
appt_info = classify_appts(appts_list)

fat_approved = [e for e in estimates_list if e.get("Status") == "APPROVED"]
fat = sum(float(e.get("Amount") or 0) for e in fat_approved)
total_evals = len(estimates_list)
approved_evals = len(fat_approved)

pending_evals = [e for e in estimates_list if e.get("Status") not in ("APPROVED", "REJECTED", "CANCELLED")]
pending_amount = sum(float(e.get("Amount") or 0) for e in pending_evals)

a_receber = max(0.0, fat - recebido)
ticket = fat / approved_evals if approved_evals else 0.0
conv_rate = (approved_evals / total_evals * 100) if total_evals else 0.0
faltas = appt_info["absences"]
primeiras = appt_info["first_consults"]
# Pacientes: tenta via agendamentos; se não encontrar, usa orçamentos como fallback
pacientes = appt_info["unique_patients"] or count_patients_from_estimates(estimates_list)
# Follow-up: cruza IDs de primeiras consultas com demais agendamentos
followup_stats = analyze_followup(appts_list, appt_info.get("first_consult_patient_ids", set()))

elapsed = (to_d - from_d).days + 1
dim_ref = calendar.monthrange(today.year, today.month)[1]
if period_type == "Mês atual":
    remaining = dim_ref - today.day
    daily_avg = fat / today.day if today.day else 0.0
    proj = fat + daily_avg * remaining
else:
    daily_avg = fat / elapsed if elapsed else 0.0
    proj = fat  # sem projeção para períodos fechados

# ── CALCULAR KPIs PERÍODO ANTERIOR ────────────────────────────────────────────
fat_approved_prev = [e for e in estimates_prev if e.get("Status") == "APPROVED"]
fat_prev = sum(float(e.get("Amount") or 0) for e in fat_approved_prev)
appts_cnt_prev = len(appts_prev)
approved_evals_prev = len(fat_approved_prev)
total_evals_prev = len(estimates_prev)
ticket_prev = fat_prev / approved_evals_prev if approved_evals_prev else 0.0
conv_rate_prev = (approved_evals_prev / total_evals_prev * 100) if total_evals_prev else 0.0
appt_info_prev = classify_appts(appts_prev)
faltas_prev = appt_info_prev["absences"]
primeiras_prev = appt_info_prev["first_consults"]
pacientes_prev = appt_info_prev["unique_patients"] or count_patients_from_estimates(estimates_prev)
a_receber_prev = max(0.0, fat_prev - recebido_prev)

pct_meta = (fat / meta * 100) if meta else 0.0

# ── DEBUG ──────────────────────────────────────────────────────────────────────
if debug_mode:
    with st.expander("🛠️ Debug — Dados da API", expanded=True):
        st.write(f"**business_id:** `{bid}` | **business_name:** `{bname}`")
        st.write(f"**Período atual:** {from_d} → {to_d} | **Anterior:** {prev_from} → {prev_to}")
        st.write(f"**Faturamento:** `{fat}` | **Recebido:** `{recebido}` | **Atendimentos:** `{appts}`")
        st.write(f"**Avaliações (orçamentos):** `{total_evals}` | **Aprovadas:** `{approved_evals}` | **Pendentes:** `{len(pending_evals)}`")
        st.write(f"**Campos detectados:** `{appt_info['field_names_found']}`")
        st.write(f"**Faltas detectadas:** `{faltas}` | **Primeiras consultas:** `{primeiras}` | **Pacientes únicos:** `{pacientes}`")
        st.write(f"**Status únicos encontrados nos agendamentos:** `{appt_info.get('raw_statuses', set())}`")
        st.write(f"**Tipos únicos encontrados nos agendamentos:** `{appt_info.get('raw_types', set())}`")
        if appts_list:
            st.write("**Campos de um agendamento:**", list(appts_list[0].keys()))
            st.write("**Exemplo de agendamento:**", appts_list[0])
        if estimates_list:
            st.write("**Campos de um orçamento:**", list(estimates_list[0].keys()))


# ── HEADER ──────────────────────────────────────────────────────────────────────
st.title(f"Dashboard — {bname}")
period_label = f"{from_d.strftime('%d/%m/%Y')} → {to_d.strftime('%d/%m/%Y')}"
if period_type == "Mês atual":
    period_label += f" | Dia {today.day} de {dim_ref} | {dim_ref - today.day} dias restantes"
st.markdown(f"**{period_label}**")

# ── PAINEL HERO — métricas principais ────────────────────────────────────────────
st.markdown("""
<style>
div[data-testid="metric-container"] {
    background: #1a1f2e;
    border: 1px solid #2d3548;
    border-radius: 12px;
    padding: 16px 20px;
}
div[data-testid="metric-container"] label {
    font-size: 0.78rem !important;
    color: #8b92a5 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
div[data-testid="metric-container"] [data-testid="stMetricValue"] {
    font-size: 1.9rem !important;
    font-weight: 700 !important;
    color: #ffffff !important;
}
div[data-testid="metric-container"] [data-testid="stMetricDelta"] {
    font-size: 0.78rem !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("### Indicadores do Período")

# ── Linha 1: Atendimentos & Pacientes ─────────────────────────────────────────
h1, h2, h3, h4 = st.columns(4)

h1.metric(
    "📅 Total Atendimentos",
    f"{appts:,}",
    delta=delta_int(appts, appts_cnt_prev),
    help="Total de agendamentos no período (não deletados)",
)
h2.metric(
    "👥 Pacientes Únicos",
    f"{pacientes:,}" if pacientes else "—",
    delta=delta_int(pacientes, pacientes_prev) if pacientes else None,
    help="Pacientes distintos com agendamento no período",
)
h3.metric(
    "⚠️ Faltas",
    f"{faltas:,}" if (faltas or appts) else "—",
    delta=delta_int(faltas, faltas_prev) if faltas else None,
    delta_color="inverse" if faltas > faltas_prev else "off",
    help="Pacientes que não compareceram, não confirmaram ou adiaram",
)
h4.metric(
    "🆕 Primeiras Consultas",
    f"{primeiras:,}" if primeiras else "—",
    delta=delta_int(primeiras, primeiras_prev) if primeiras else None,
    help="Pacientes novos que vieram pela primeira vez",
)

st.markdown("")

# ── Linha 2: Financeiro & Conversão ───────────────────────────────────────────
h5, h6, h7, h8 = st.columns(4)

h5.metric(
    "📋 Avaliações no Mês",
    f"{total_evals:,}",
    delta=delta_int(total_evals, total_evals_prev),
    help="Orçamentos únicos gerados no período (avaliações realizadas)",
)
h6.metric(
    "📈 Taxa de Conversão",
    f"{conv_rate:.1f}%",
    delta=delta_pct(conv_rate, conv_rate_prev),
    help="Orçamentos aprovados ÷ orçamentos gerados no período",
)
h7.metric(
    "🎟️ Ticket Médio",
    fmt_brl(ticket),
    delta=delta_color(ticket, ticket_prev),
    help="Valor médio por orçamento aprovado",
)
h8.metric(
    "⏳ Oportunidades Abertas",
    f"{len(pending_evals):,}",
    delta=fmt_brl(pending_amount),
    help="Orçamentos gerados e ainda não aprovados (potencial de receita)",
)

# Barra de progresso da meta compacta
st.markdown("")
icon_meta = "🟢" if pct_meta >= 100 else ("🟡" if pct_meta >= 70 else "🔴")
st.markdown(
    f"{icon_meta} **Meta:** {fmt_brl(fat)} / {fmt_brl(meta)} &nbsp;·&nbsp; "
    f"**{pct_meta:.1f}%** atingido &nbsp;·&nbsp; "
    f"{'Superou em ' + fmt_brl(fat - meta) if fat >= meta else 'Faltam ' + fmt_brl(meta - fat)}"
)
st.progress(min(pct_meta / 100, 1.0))

if faltas == 0 and appts > 0 and not appt_info.get("field_names_found", {}).get("status_field"):
    st.caption(
        "ℹ️ **Faltas** e **Primeiras Consultas** mostram `—` porque o campo de status "
        "dos agendamentos ainda não foi identificado. Ative o **Modo debug** na sidebar "
        "para ver os campos reais da API e ajustar a detecção."
    )

# ── Evolução da Meta ────────────────────────────────────────────────────────────
st.markdown("### 📈 Evolução da Meta")

_first_ref = date(today.year, today.month, 1)
_all_days  = pd.date_range(from_d, to_d, freq="D")
_n_days    = len(_all_days)

if fat_approved:
    _df = pd.DataFrame(fat_approved)
    # Tenta detectar coluna de data
    _date_col = None
    for _c in ("SearchDate", "Date", "date", "DataAprovacao", "CreatedAt", "created_at"):
        if _c in _df.columns:
            _date_col = _c
            break
    if _date_col:
        _df["_d"] = pd.to_datetime(_df[_date_col], errors="coerce").dt.date
        _df["Amount"] = pd.to_numeric(_df["Amount"], errors="coerce").fillna(0)
        _daily = _df.groupby("_d")["Amount"].sum()
        _cum, _actuals = 0.0, []
        for _day in _all_days:
            _cum += float(_daily.get(_day.date(), 0))
            _actuals.append(_cum)
    else:
        # Distribui linearmente se não achar coluna de data
        _actuals = [fat * (i / _n_days) for i in range(1, _n_days + 1)]
else:
    _actuals = [0.0] * _n_days

# Linha de meta acumulada pro-rata
_targets = [(meta / _n_days) * i for i in range(1, _n_days + 1)]

_fig_meta = go.Figure()
_fig_meta.add_trace(go.Scatter(
    x=[d.date() for d in _all_days], y=_targets,
    name="Meta", mode="lines",
    line=dict(color="#FF6B6B", width=2, dash="dash"),
))
_fig_meta.add_trace(go.Scatter(
    x=[d.date() for d in _all_days], y=_actuals,
    name="Faturamento Real", mode="lines+markers",
    line=dict(color="#2E86AB", width=2.5),
    marker=dict(size=5),
    fill="tozeroy", fillcolor="rgba(46,134,171,0.12)",
))
# Linha da meta total
_fig_meta.add_hline(
    y=meta, line_dash="dot", line_color="rgba(255,107,107,0.4)",
    annotation_text=f"Meta total: {fmt_brl(meta)}",
    annotation_position="top left",
)
_fig_meta.update_layout(
    height=300,
    margin=dict(l=0, r=0, t=10, b=0),
    hovermode="x unified",
    legend=dict(orientation="h", y=1.12, x=0),
    yaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
    xaxis=dict(tickformat="%d/%m"),
    plot_bgcolor="rgba(0,0,0,0)",
    paper_bgcolor="rgba(0,0,0,0)",
)
st.plotly_chart(_fig_meta, use_container_width=True)

st.divider()

# ── ABAS PRINCIPAIS ──────────────────────────────────────────────────────────────
page_overview, page_opp, page_prof, page_price, page_prev = st.tabs([
    "📊 Visão Geral",
    "🎯 Oportunidades",
    "👨‍⚕️ Profissionais",
    "💰 Precificação",
    "🛡️ Prevenção",
])


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 1 — VISÃO GERAL
# ═══════════════════════════════════════════════════════════════════════════════
with page_overview:

    # Linha 1: Financeiro
    st.subheader("💰 Financeiro")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Faturamento (Aprovado)", fmt_brl(fat),
              delta=delta_color(fat, fat_prev))
    c2.metric("Recebido", fmt_brl(recebido),
              delta=delta_color(recebido, recebido_prev))
    c3.metric("Meta", fmt_brl(meta),
              delta=f"{pct_meta:.1f}% atingido")
    c4.metric("Projeção do Mês", fmt_brl(proj),
              help="Baseada na média diária atual" if period_type == "Mês atual" else "Período encerrado")

    st.divider()

    # Linha 2: Pacientes
    st.subheader("👥 Pacientes & Avaliações")
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Pacientes Únicos", f"{pacientes:,}",
              delta=delta_int(pacientes, pacientes_prev))
    c6.metric("Avaliações Feitas", f"{total_evals:,}",
              delta=delta_int(total_evals, total_evals_prev))
    c7.metric("Ticket Médio", fmt_brl(ticket),
              delta=delta_color(ticket, ticket_prev))
    c8.metric("Taxa de Conversão", fmt_pct(conv_rate),
              delta=delta_pct(conv_rate, conv_rate_prev))

    st.divider()

    # Linha 3: Operacional
    st.subheader("📋 Operacional")
    c9, c10, c11, c12 = st.columns(4)
    c9.metric("Atendimentos", f"{appts:,}",
              delta=delta_int(appts, appts_cnt_prev))
    c10.metric("Primeiras Consultas", f"{primeiras:,}",
               delta=delta_int(primeiras, primeiras_prev),
               help="Baseado no tipo de agendamento")
    c11.metric("Faltas", f"{faltas:,}",
               delta=delta_int(faltas, faltas_prev),
               delta_color="inverse" if faltas > faltas_prev else "normal")
    c12.metric("A Receber", fmt_brl(a_receber),
               delta=delta_color(a_receber, a_receber_prev),
               delta_color="inverse")

    st.divider()

    # Barra de progresso da meta
    icon = "🟢" if pct_meta >= 100 else ("🟡" if pct_meta >= 70 else "🔴")
    col_bar, col_proj = st.columns([5, 1])
    with col_bar:
        if fat >= meta:
            st.markdown(f"**{icon} Meta atingida! {pct_meta:.1f}% — superou em {fmt_brl(fat - meta)}**")
        else:
            st.markdown(
                f"**{icon} Meta: {pct_meta:.1f}% atingido**"
                f" — faltam **{fmt_brl(meta - fat)}** para bater"
            )
        st.progress(min(pct_meta / 100, 1.0))
    with col_proj:
        st.metric("Média Diária", fmt_brl(daily_avg))

    # Comparativo Aprovado x Recebido
    st.divider()
    col_comp, col_info = st.columns([3, 1])
    with col_comp:
        fig_comp = go.Figure()
        fig_comp.add_trace(go.Bar(
            name="Aprovado", x=["Período"],
            y=[fat], marker_color="#2E86AB",
            text=[fmt_brl(fat)], textposition="outside",
        ))
        fig_comp.add_trace(go.Bar(
            name="Recebido", x=["Período"],
            y=[recebido], marker_color="#4CAF50",
            text=[fmt_brl(recebido)], textposition="outside",
        ))
        fig_comp.add_trace(go.Bar(
            name="A Receber", x=["Período"],
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
        receb_pct = (recebido / fat * 100) if fat else 0
        st.metric("% Recebido", f"{receb_pct:.1f}%")
        st.metric("A Receber", fmt_brl(a_receber))
        st.metric("vs Meta", fmt_brl(fat - meta),
                  delta_color="normal" if fat >= meta else "inverse")

    # Evolução diária (apenas para períodos com data de início definida)
    if period_type == "Mês atual" and fat_approved:
        st.divider()
        st.subheader("📈 Evolução Diária do Faturamento")
        first = date(today.year, today.month, 1)
        all_days = pd.date_range(first, today, freq="D")
        n_days_evol = len(all_days)

        df_fat_daily = pd.DataFrame(fat_approved)
        df_fat_daily["_date"] = pd.to_datetime(
            df_fat_daily.get("SearchDate", df_fat_daily.get("Date")),
            errors="coerce"
        ).dt.date
        df_fat_daily["Amount"] = pd.to_numeric(df_fat_daily["Amount"], errors="coerce").fillna(0)
        daily_sum = df_fat_daily.groupby("_date")["Amount"].sum()
        cumulative = 0.0
        actuals = []
        for d in all_days:
            cumulative += float(daily_sum.get(d.date(), 0))
            actuals.append(cumulative)

        full_month = pd.date_range(first, date(today.year, today.month, dim_ref), freq="D")
        targets = [(meta / dim_ref) * i for i in range(1, dim_ref + 1)]

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

    # Comparativo mensal 6 meses
    st.divider()
    st.subheader("📊 Comparativo — Últimos 6 Meses")

    rows = []
    with st.spinner("Carregando histórico dos últimos 6 meses..."):
        for i in range(5, 0, -1):
            s, e = nth_month_back(today, i)
            ests, _ = fetch_estimates(sub, aid, tok, bid, s, e)
            al, _   = fetch_appt_list(sub, aid, tok, bid, s, e)
            appt_info_hist = classify_appts(al)
            fat_hist = sum(float(x.get("Amount") or 0) for x in ests if x.get("Status") == "APPROVED")
            apprv_hist = len([x for x in ests if x.get("Status") == "APPROVED"])
            total_hist = len(ests)
            rec_hist, _ = fetch_revenue(sub, aid, tok, bid, s, e)
            conv_hist = (apprv_hist / total_hist * 100) if total_hist else 0.0
            faltas_hist = appt_info_hist["absences"]
            rows.append({
                "Mês": s.strftime("%b/%y"),
                "Faturamento": fat_hist,
                "Recebido": rec_hist,
                "Atendimentos": len(al),
                "Ticket": fat_hist / apprv_hist if apprv_hist else 0.0,
                "Conversão": conv_hist,
                "Faltas": faltas_hist,
                "atual": False,
            })

    rows.append({
        "Mês": today.strftime("%b/%y"),
        "Faturamento": fat,
        "Recebido": recebido,
        "Atendimentos": appts,
        "Ticket": ticket,
        "Conversão": conv_rate,
        "Faltas": faltas,
        "atual": True,
    })

    df_m = pd.DataFrame(rows)
    bar_colors = ["#FF6B6B" if r["atual"] else "#2E86AB" for _, r in df_m.iterrows()]

    tab_f, tab_a, tab_t, tab_conv, tab_flt = st.tabs([
        "💰 Faturamento", "👥 Atendimentos", "🎟️ Ticket Médio",
        "📈 Conversão", "⚠️ Faltas"
    ])

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

    with tab_conv:
        bc = go.Figure(go.Bar(
            x=df_m["Mês"], y=df_m["Conversão"], marker_color=bar_colors,
            text=[f"{v:.1f}%" for v in df_m["Conversão"]], textposition="outside",
        ))
        bc.add_hline(y=70, line_dash="dot", line_color="#FF6B6B",
                     annotation_text="Meta 70%")
        bc.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0),
                         yaxis=dict(ticksuffix="%"))
        st.plotly_chart(bc, use_container_width=True)

    with tab_flt:
        bfl = go.Figure(go.Bar(
            x=df_m["Mês"], y=df_m["Faltas"], marker_color=bar_colors,
            text=df_m["Faltas"], textposition="outside",
        ))
        bfl.update_layout(height=300, margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(bfl, use_container_width=True)
        if all(v == 0 for v in df_m["Faltas"]):
            st.caption("ℹ️ Dados de faltas dependem do campo de status nos agendamentos. Ative o modo debug para verificar os campos disponíveis.")

    st.caption(
        f"Média diária: **{fmt_brl(daily_avg)}** &nbsp;|&nbsp; "
        f"Projeção: **{fmt_brl(proj)}** &nbsp;|&nbsp; "
        f"Dados via API Clinicorp"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 2 — OPORTUNIDADES
# ═══════════════════════════════════════════════════════════════════════════════
with page_opp:
    st.subheader("🎯 Funil de Conversão")

    # KPIs de Oportunidades
    oa1, oa2, oa3, oa4 = st.columns(4)
    oa1.metric("📋 Avaliações Feitas", f"{total_evals:,}",
               delta=delta_int(total_evals, total_evals_prev))
    oa2.metric("✅ Aprovadas", f"{approved_evals:,}",
               delta=delta_int(approved_evals, approved_evals_prev))
    oa3.metric("⏳ Pendentes", f"{len(pending_evals):,}",
               help=f"Valor em aberto: {fmt_brl(pending_amount)}")
    oa4.metric("📈 Taxa de Conversão", fmt_pct(conv_rate),
               delta=delta_pct(conv_rate, conv_rate_prev))

    # Funil
    if total_evals > 0:
        col_funil, col_stats = st.columns([2, 1])
        with col_funil:
            rejected = len([e for e in estimates_list
                            if e.get("Status") in ("REJECTED", "CANCELLED")])
            fig_funil = go.Figure(go.Funnel(
                y=["Avaliações", "Pendentes", "Aprovadas"],
                x=[total_evals, len(pending_evals), approved_evals],
                textinfo="value+percent initial",
                marker=dict(color=["#2E86AB", "#FFB74D", "#4CAF50"]),
            ))
            fig_funil.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_funil, use_container_width=True)

        with col_stats:
            st.metric("💰 Valor Aprovado", fmt_brl(fat))
            st.metric("⏳ Valor Pendente", fmt_brl(pending_amount))
            total_orc = sum(float(e.get("Amount") or 0) for e in estimates_list)
            st.metric("📊 Total Orçado", fmt_brl(total_orc))
            if rejected > 0:
                st.metric("❌ Recusados/Cancelados", f"{rejected:,}")
    else:
        st.info("Nenhuma avaliação encontrada neste período.")

    # Tabela de pendentes
    if pending_evals:
        st.divider()
        st.subheader("📋 Orçamentos Pendentes (Oportunidades em Aberto)")
        df_pend = pd.DataFrame(pending_evals)

        # Selecionar colunas relevantes
        cols_show = []
        for c in ["ProfessionalName", "PatientName", "ClientName", "SearchDate",
                  "Date", "Amount", "Status", "id"]:
            if c in df_pend.columns:
                cols_show.append(c)
        if cols_show:
            df_show = df_pend[cols_show].copy()
            if "Amount" in df_show.columns:
                df_show["Amount"] = pd.to_numeric(df_show["Amount"], errors="coerce")
            st.dataframe(
                df_show.sort_values("Amount", ascending=False)
                if "Amount" in df_show.columns else df_show,
                use_container_width=True,
                height=300,
            )
            st.caption(f"Total de {len(pending_evals)} orçamentos pendentes | Valor total: {fmt_brl(pending_amount)}")
    else:
        st.divider()
        st.info("Nenhum orçamento pendente no período.")

    # Recuperação de Faltas
    st.divider()
    st.subheader("⚠️ Recuperação de Faltas")
    col_f1, col_f2, col_f3 = st.columns(3)
    receita_perdida = faltas * ticket if ticket else 0.0
    taxa_falta = (faltas / appts * 100) if appts else 0.0

    col_f1.metric("Faltas no Período", f"{faltas:,}")
    col_f2.metric("Taxa de Falta", fmt_pct(taxa_falta))
    col_f3.metric("Receita Potencial Perdida", fmt_brl(receita_perdida),
                  help="Faltas × Ticket médio")

    if faltas > 0:
        st.warning(
            f"**{faltas} falta(s)** representam uma perda potencial de **{fmt_brl(receita_perdida)}**. "
            "Recomenda-se contato proativo para reagendamento dentro de 48h."
        )
    elif appts > 0:
        st.success("Nenhuma falta detectada no período. Ative o modo debug para verificar os campos de status.")
    else:
        st.info("Ative o modo debug para verificar se o campo de status de faltas está disponível na API.")

    # ── Análise: Primeira Consulta → Follow-up ─────────────────────────────────
    st.divider()
    st.subheader("🔄 Primeira Consulta → Follow-up")

    if not followup_stats.get("available"):
        st.info(
            f"**{primeiras} primeira(s) consulta(s)** identificada(s) no período. "
            "A análise de follow-up requer que o campo de **tipo de agendamento** "
            "seja detectado na API. Ative o **Modo debug** para ver os campos disponíveis."
        )
        if primeiras > 0:
            st.caption(
                "Dica: o campo de tipo pode se chamar `Type`, `TipoConsulta`, `AppointmentType` "
                "ou similar. Compartilhe o output do debug para ajuste."
            )
    else:
        total_fc  = followup_stats["total_first_consult"]
        com_fu    = followup_stats["with_followup"]
        sem_fu    = followup_stats["without_followup"]
        taxa_fu   = followup_stats["followup_rate"]

        fu1, fu2, fu3, fu4 = st.columns(4)
        fu1.metric("🆕 Primeiras Consultas", f"{total_fc:,}")
        fu2.metric("✅ Fizeram Follow-up", f"{com_fu:,}",
                   help="Paciente da 1ª consulta que voltou ou tem retorno agendado no período")
        fu3.metric("⏳ Sem Follow-up", f"{sem_fu:,}",
                   delta=f"Oportunidade: {fmt_brl(sem_fu * ticket)}" if ticket else None,
                   delta_color="off",
                   help="Pacientes que vieram uma vez e não retornaram no período")
        fu4.metric("📈 Taxa de Follow-up", fmt_pct(taxa_fu),
                   delta_color="normal" if taxa_fu >= 60 else "inverse")

        # Funil visual
        if total_fc > 0:
            fig_fu = go.Figure(go.Funnel(
                y=["Primeiras Consultas", "Com Follow-up"],
                x=[total_fc, com_fu],
                textinfo="value+percent initial",
                marker=dict(color=["#2E86AB", "#4CAF50"]),
            ))
            fig_fu.update_layout(height=220, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_fu, use_container_width=True)

        # Alerta e orientação
        if sem_fu > 0:
            receita_potencial = sem_fu * ticket
            st.warning(
                f"**{sem_fu} paciente(s)** vieram pela primeira vez e ainda não retornaram. "
                f"Receita potencial em aberto: **{fmt_brl(receita_potencial)}**. "
                "Recomenda-se contato de follow-up ativo."
            )
        elif total_fc > 0:
            st.success(f"Todos os {total_fc} pacientes de primeira consulta já fizeram follow-up!")

        st.caption(
            "ℹ️ Follow-up = paciente que teve uma 1ª consulta e possui pelo menos mais um "
            "agendamento no mesmo período. Para rastrear retornos fora do período atual, "
            "amplie o filtro de datas na sidebar."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 3 — PROFISSIONAIS
# ═══════════════════════════════════════════════════════════════════════════════
with page_prof:
    st.subheader("👨‍⚕️ Desempenho por Profissional")

    if not estimates_list:
        st.info("Nenhuma avaliação encontrada neste período.")
    else:
        df_est = pd.DataFrame(estimates_list)

        # Detectar coluna de profissional
        prof_col = None
        for c in ("ProfessionalName", "professionalName", "Professional",
                  "DoctorName", "Profissional", "profissional"):
            if c in df_est.columns:
                prof_col = c
                break

        if not prof_col:
            st.warning("Campo de profissional não encontrado nos dados. Ative o modo debug para ver os campos disponíveis.")
            if debug_mode and estimates_list:
                st.write("Campos disponíveis nos orçamentos:", list(df_est.columns))
        else:
            df_est["Amount"] = pd.to_numeric(df_est["Amount"], errors="coerce").fillna(0)
            df_est[prof_col] = df_est[prof_col].fillna("(sem nome)")

            # Dropdown filtro
            profissionais = ["Todos"] + sorted(df_est[prof_col].unique().tolist())
            prof_sel = st.selectbox("Filtrar por profissional", profissionais)

            df_filtered = df_est if prof_sel == "Todos" else df_est[df_est[prof_col] == prof_sel]

            # Se profissional individual: KPIs individuais
            if prof_sel != "Todos":
                fat_p = sum(df_filtered[df_filtered["Status"] == "APPROVED"]["Amount"])
                evals_p = len(df_filtered)
                apprv_p = len(df_filtered[df_filtered["Status"] == "APPROVED"])
                pend_p = len([e for e in df_filtered.to_dict("records")
                              if e.get("Status") not in ("APPROVED", "REJECTED", "CANCELLED")])
                ticket_p = fat_p / apprv_p if apprv_p else 0.0
                conv_p = (apprv_p / evals_p * 100) if evals_p else 0.0

                st.divider()
                p1, p2, p3, p4, p5 = st.columns(5)
                p1.metric("Valor Aprovado", fmt_brl(fat_p))
                p2.metric("Avaliações", f"{evals_p:,}")
                p3.metric("Aprovadas", f"{apprv_p:,}")
                p4.metric("Ticket Médio", fmt_brl(ticket_p))
                p5.metric("Taxa Conversão", fmt_pct(conv_p))
                st.divider()

            # Tabela resumo por profissional
            df_prof = df_est.groupby(prof_col, as_index=False).apply(
                lambda g: pd.Series({
                    "Avaliações": len(g),
                    "Aprovadas": len(g[g["Status"] == "APPROVED"]),
                    "Pendentes": len([e for e in g.to_dict("records")
                                      if e.get("Status") not in ("APPROVED", "REJECTED", "CANCELLED")]),
                    "Valor Aprovado": g[g["Status"] == "APPROVED"]["Amount"].sum(),
                    "Ticket Médio": (g[g["Status"] == "APPROVED"]["Amount"].sum() /
                                     len(g[g["Status"] == "APPROVED"])
                                     if len(g[g["Status"] == "APPROVED"]) > 0 else 0.0),
                    "Conversão (%)": (len(g[g["Status"] == "APPROVED"]) / len(g) * 100) if len(g) else 0.0,
                })
            ).reset_index(drop=True)

            # 4 sub-abas
            tp1, tp2, tp3, tp4 = st.tabs([
                "💰 Valor Aprovado", "🎟️ Ticket Médio",
                "📈 Conversão", "📋 Avaliações"
            ])

            with tp1:
                df_r = df_prof.sort_values("Valor Aprovado", ascending=True)
                fig_r = go.Figure(go.Bar(
                    x=df_r["Valor Aprovado"], y=df_r[prof_col],
                    orientation="h", marker_color="#2E86AB",
                    text=[fmt_brl(v) for v in df_r["Valor Aprovado"]],
                    textposition="outside",
                ))
                fig_r.update_layout(
                    height=max(300, len(df_r) * 55),
                    margin=dict(l=0, r=120, t=10, b=0),
                    xaxis=dict(tickprefix="R$ ", tickformat=",.0f"),
                )
                st.plotly_chart(fig_r, use_container_width=True)

            with tp2:
                df_t = df_prof.sort_values("Ticket Médio", ascending=True)
                fig_t = go.Figure(go.Bar(
                    x=df_t["Ticket Médio"], y=df_t[prof_col],
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
                df_cv = df_prof.sort_values("Conversão (%)", ascending=True)
                fig_cv = go.Figure(go.Bar(
                    x=df_cv["Conversão (%)"], y=df_cv[prof_col],
                    orientation="h", marker_color="#9B59B6",
                    text=[f"{v:.1f}%" for v in df_cv["Conversão (%)"]],
                    textposition="outside",
                ))
                fig_cv.add_vline(x=70, line_dash="dot", line_color="#FF6B6B",
                                 annotation_text="Meta 70%")
                fig_cv.update_layout(
                    height=max(300, len(df_cv) * 55),
                    margin=dict(l=0, r=80, t=10, b=0),
                    xaxis=dict(ticksuffix="%"),
                )
                st.plotly_chart(fig_cv, use_container_width=True)

            with tp4:
                df_ev = df_prof.sort_values("Avaliações", ascending=True)
                fig_ev = go.Figure(go.Bar(
                    x=df_ev["Avaliações"], y=df_ev[prof_col],
                    orientation="h", marker_color="#FF6B6B",
                    text=df_ev["Avaliações"], textposition="outside",
                ))
                fig_ev.update_layout(
                    height=max(300, len(df_ev) * 55),
                    margin=dict(l=0, r=60, t=10, b=0),
                )
                st.plotly_chart(fig_ev, use_container_width=True)

            # Tabela resumo completa
            st.divider()
            st.subheader("📊 Resumo Completo por Profissional")
            df_display = df_prof.copy()
            df_display["Valor Aprovado"] = df_display["Valor Aprovado"].apply(fmt_brl)
            df_display["Ticket Médio"] = df_display["Ticket Médio"].apply(fmt_brl)
            df_display["Conversão (%)"] = df_display["Conversão (%)"].apply(lambda x: f"{x:.1f}%")
            st.dataframe(df_display, use_container_width=True)


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 4 — PRECIFICAÇÃO
# ═══════════════════════════════════════════════════════════════════════════════
with page_price:
    st.subheader("💰 Tabela de Precificação")
    st.caption("Preços dos procedimentos com controle de reajuste semestral.")

    procedures, price_history = load_pricing()

    # Alertas de reajuste vencido
    today_str = today.isoformat()
    overdue = [p for p in procedures
               if p.get("Próxima Atualização", "9999-99-99") <= today_str]
    if overdue:
        st.warning(
            f"⚠️ **{len(overdue)} procedimento(s)** com reajuste vencido ou pendente! "
            "Revise os preços abaixo."
        )

    # Editor de tabela
    df_pricing = pd.DataFrame(procedures)
    if df_pricing.empty:
        df_pricing = pd.DataFrame(columns=[
            "Procedimento", "Preço Atual", "Última Atualização",
            "Próxima Atualização", "Notas"
        ])

    edited_df = st.data_editor(
        df_pricing,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Procedimento": st.column_config.TextColumn("Procedimento", width="large"),
            "Preço Atual": st.column_config.NumberColumn(
                "Preço Atual (R$)", format="R$ %.2f", min_value=0.0
            ),
            "Última Atualização": st.column_config.DateColumn("Última Atualização"),
            "Próxima Atualização": st.column_config.DateColumn("Próxima Atualização"),
            "Notas": st.column_config.TextColumn("Notas", width="medium"),
        },
        hide_index=True,
    )

    col_save, col_spacer = st.columns([1, 3])
    with col_save:
        if st.button("💾 Salvar Tabela", type="primary", use_container_width=True):
            save_pricing(edited_df.to_dict("records"), price_history)
            st.success("Tabela de preços salva com sucesso!")
            st.rerun()

    # Simulador de Reajuste
    st.divider()
    st.subheader("📈 Simulador de Reajuste Semestral")

    col_slider, col_btn = st.columns([3, 1])
    with col_slider:
        reajuste_pct = st.slider(
            "Percentual de reajuste (%)",
            min_value=0.0, max_value=50.0, value=8.0, step=0.5,
            format="%.1f%%"
        )
    with col_btn:
        st.write("")
        st.write("")
        aplicar = st.button("✅ Aplicar Reajuste", type="primary", use_container_width=True)

    if not edited_df.empty and "Preço Atual" in edited_df.columns:
        df_sim = edited_df[["Procedimento", "Preço Atual"]].copy()
        df_sim["Preço Atual"] = pd.to_numeric(df_sim["Preço Atual"], errors="coerce").fillna(0)
        df_sim["Preço Novo"] = df_sim["Preço Atual"] * (1 + reajuste_pct / 100)
        df_sim["Aumento (R$)"] = df_sim["Preço Novo"] - df_sim["Preço Atual"]
        df_sim["Aumento (%)"] = reajuste_pct

        st.dataframe(
            df_sim.style.format({
                "Preço Atual": "R$ {:.2f}",
                "Preço Novo": "R$ {:.2f}",
                "Aumento (R$)": "R$ {:.2f}",
                "Aumento (%)": "{:.1f}%",
            }),
            use_container_width=True,
            hide_index=True,
        )

        if aplicar:
            nova_data_atual = today.isoformat()
            nova_proxima = (today + timedelta(days=180)).isoformat()

            # Registrar histórico
            hist_entry = {
                "data_reajuste": nova_data_atual,
                "percentual": reajuste_pct,
                "procedimentos": [
                    {
                        "nome": row["Procedimento"],
                        "preco_anterior": row["Preço Atual"],
                        "preco_novo": row["Preço Novo"],
                    }
                    for _, row in df_sim.iterrows()
                ],
            }
            price_history.append(hist_entry)

            # Atualizar preços
            updated_procs = []
            price_map = {row["Procedimento"]: row["Preço Novo"] for _, row in df_sim.iterrows()}
            for proc in edited_df.to_dict("records"):
                nome = proc.get("Procedimento", "")
                if nome in price_map:
                    proc["Preço Atual"] = round(price_map[nome], 2)
                    proc["Última Atualização"] = nova_data_atual
                    proc["Próxima Atualização"] = nova_proxima
                updated_procs.append(proc)

            save_pricing(updated_procs, price_history)
            st.success(
                f"Reajuste de **{reajuste_pct:.1f}%** aplicado! "
                f"Próximo reajuste sugerido: **{nova_proxima}**"
            )
            st.rerun()

    # Histórico de reajustes
    if price_history:
        st.divider()
        st.subheader("📜 Histórico de Reajustes")
        for entry in reversed(price_history[-5:]):
            with st.expander(f"Reajuste de {entry.get('percentual', 0):.1f}% em {entry.get('data_reajuste', '?')}"):
                df_hist = pd.DataFrame(entry.get("procedimentos", []))
                if not df_hist.empty:
                    st.dataframe(df_hist, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════════
# ABA 5 — PREVENÇÃO
# ═══════════════════════════════════════════════════════════════════════════════
with page_prev:
    st.subheader("🛡️ Prevenção & Protocolos")

    # ── KPIs de Saúde do Negócio ────────────────────────────────────────────────
    st.subheader("📊 Indicadores de Saúde Operacional")
    taxa_retorno = 0.0  # sem dados de retorno na API atual
    receita_em_risco = pending_amount

    pv1, pv2, pv3, pv4 = st.columns(4)
    pv1.metric("Taxa de Falta", fmt_pct(taxa_falta),
               delta_color="inverse" if taxa_falta > 10 else "normal")
    pv2.metric("Taxa de Retorno", "N/D",
               help="Dados de retorno não disponíveis via API atual")
    pv3.metric("Taxa de Conversão", fmt_pct(conv_rate),
               delta_color="normal" if conv_rate >= 70 else "inverse")
    pv4.metric("Receita em Risco", fmt_brl(receita_em_risco),
               help="Valor dos orçamentos pendentes (não aprovados)")

    # ── Alertas Automáticos ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("🚨 Alertas Automáticos")

    has_alert = False

    if taxa_falta > 15:
        st.error(f"**CRÍTICO — Taxa de Falta:** {taxa_falta:.1f}% (acima de 15%). Revisar protocolo de confirmação urgente.")
        has_alert = True
    elif taxa_falta > 10:
        st.warning(f"**ATENÇÃO — Taxa de Falta:** {taxa_falta:.1f}% (acima de 10%). Reforçar confirmações de consulta.")
        has_alert = True

    if conv_rate < 50 and total_evals > 0:
        st.error(f"**CRÍTICO — Conversão:** {conv_rate:.1f}% (abaixo de 50%). Revisar abordagem de vendas urgente.")
        has_alert = True
    elif conv_rate < 70 and total_evals > 0:
        st.warning(f"**ATENÇÃO — Conversão:** {conv_rate:.1f}% (abaixo de 70%). Reforçar follow-up e facilitar parcelamento.")
        has_alert = True

    if meta > 0 and len(pending_evals) > 0:
        pend_pct = (pending_amount / meta * 100)
        if pend_pct > 30:
            st.warning(
                f"**ATENÇÃO — Pendentes:** {fmt_brl(pending_amount)} ({pend_pct:.1f}% da meta) em aberto. "
                "Acionar follow-up com pacientes pendentes."
            )
            has_alert = True

    if meta > 0 and pct_meta < 70:
        st.error(
            f"**CRÍTICO — Meta:** apenas {pct_meta:.1f}% atingido. "
            f"Faltam {fmt_brl(meta - fat)} para a meta de {fmt_brl(meta)}."
        )
        has_alert = True
    elif meta > 0 and pct_meta < 90:
        st.warning(
            f"**ATENÇÃO — Meta:** {pct_meta:.1f}% atingido. "
            f"Faltam {fmt_brl(meta - fat)} para a meta."
        )
        has_alert = True

    if not has_alert:
        st.success("Todos os indicadores operacionais estão dentro dos parâmetros normais.")

    # ── Protocolos Operacionais ─────────────────────────────────────────────────
    st.divider()
    st.subheader("📋 Protocolos Operacionais")

    col_p1, col_p2, col_p3 = st.columns(3)

    with col_p1:
        st.markdown("**🔔 Protocolo de Confirmação**")
        st.checkbox("Confirmar consultas com 48h de antecedência", key="confirm_48h")
        st.checkbox("Reconfirmar com 24h de antecedência", key="confirm_24h")
        st.checkbox("Registrar confirmação no sistema", key="confirm_log")
        st.checkbox("Lembrete automático por WhatsApp/SMS", key="confirm_wpp")
        st.checkbox("Protocolo de lista de espera para faltas", key="confirm_waitlist")

    with col_p2:
        st.markdown("**💼 Protocolo de Conversão**")
        st.checkbox("Follow-up em 24h após avaliação", key="conv_followup")
        st.checkbox("Apresentar opções de parcelamento", key="conv_parc")
        st.checkbox("Verificar pendentes com mais de 7 dias", key="conv_7d")
        st.checkbox("Contato personalizado para orçamentos > R$2.000", key="conv_vip")
        st.checkbox("Registrar motivo de recusa", key="conv_motivo")

    with col_p3:
        st.markdown("**🔄 Protocolo de Retenção**")
        st.checkbox("Reagendamento imediato após consulta", key="ret_reasch")
        st.checkbox("Reativar pacientes inativos (>6 meses)", key="ret_reativ")
        st.checkbox("Lembrete de manutenção/retorno", key="ret_lembrete")
        st.checkbox("Pesquisa de satisfação pós-consulta", key="ret_pesq")
        st.checkbox("Programa de indicação de pacientes", key="ret_indica")

    # ── Protocolos Clínicos ─────────────────────────────────────────────────────
    st.divider()
    st.subheader("🦷 Protocolos Clínicos de Prevenção")

    col_c1, col_c2 = st.columns(2)

    with col_c1:
        st.markdown("**📅 Rotina Preventiva**")
        st.checkbox("Limpeza a cada 6 meses (todos os pacientes)", key="clin_limpeza")
        st.checkbox("Aplicação de flúor para pacientes de risco", key="clin_fluor")
        st.checkbox("Radiografias periapicais anuais", key="clin_rx_periap")
        st.checkbox("Radiografia panorâmica a cada 3 anos", key="clin_rx_pan")
        st.checkbox("Consulta preventiva anual", key="clin_prev_anual")

        st.markdown("**🩺 Doenças Crônicas**")
        st.checkbox("Controle de pacientes diabéticos", key="clin_diabetes")
        st.checkbox("Protocolo para hipertensos", key="clin_hiper")
        st.checkbox("Acompanhamento de pacientes com bruxismo", key="clin_bruxismo")
        st.checkbox("Triagem de lesões suspeitas (câncer oral)", key="clin_cancer")

    with col_c2:
        st.markdown("**👶 Protocolos por Faixa Etária**")
        st.checkbox("Orientação de higiene para crianças (0-12 anos)", key="clin_crian")
        st.checkbox("Selante para molares de crianças", key="clin_selante")
        st.checkbox("Avaliação ortodôntica em adolescentes", key="clin_orto")
        st.checkbox("Rastreio de doenças periodontais em adultos", key="clin_perio")
        st.checkbox("Protocolo geriátrico (próteses, xerostomia)", key="clin_geriat")

        st.markdown("**📝 Documentação**")
        st.checkbox("Anamnese atualizada a cada 12 meses", key="clin_anam")
        st.checkbox("Odontograma atualizado a cada consulta", key="clin_odonto")
        st.checkbox("Foto clínica inicial e de acompanhamento", key="clin_foto")
        st.checkbox("Consentimento informado assinado", key="clin_consent")

    # Observações livres
    st.divider()
    st.subheader("📝 Observações e Ações")
    obs = st.text_area(
        "Registre observações, ações em andamento ou pendências:",
        placeholder="Ex: Paciente João Silva — orçamento pendente desde 15/09, ligar até sexta-feira...",
        height=120,
        key="prevention_obs",
    )

    if obs:
        st.info(f"📌 Observação registrada: _{obs[:100]}{'...' if len(obs) > 100 else ''}_")
        st.caption("Nota: As observações são mantidas na sessão atual. Para persistência, copie para um documento externo.")
