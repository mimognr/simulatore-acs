import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import io

# Impostazione layout della pagina web a schermo intero
st.set_page_config(layout="wide")

st.title("Simulatore ACS")
st.write("Sviluppato in Python + Streamlit per simulazioni avanzate ad inseguimento di fase.")

# ==========================================
# 1. BARRA LATERALE: PARAMETRI DI INPUT
# ==========================================
st.sidebar.header("Parametri Macchina ed Impianto")

V1 = st.sidebar.number_input("Volume Puffer 1 (l)", min_value=10, value=186)
V2 = st.sidebar.number_input("Volume Puffer 2 (l)", min_value=10, value=750)
P1 = st.sidebar.number_input("Potenza PdC 1 (kW)", min_value=0.0, value=10.0)
P2 = st.sidebar.number_input("Potenza PdC 2 (kW)", min_value=0.0, value=23.0)

st.sidebar.markdown("---")
st.sidebar.header("Idraulica e Scambio")
V_primario = st.sidebar.number_input("Portata Primaria (l/min)", min_value=1.0, value=41.67) # 2.5 m3/h
V_split = st.sidebar.number_input("Portata Split verso T1 (l/min)", min_value=0.0, value=15.0)
T_cold = st.sidebar.number_input("T Ingresso ACS Fredda (°C)", min_value=0.0, value=10.0)
T_setpoint = st.sidebar.number_input("T Uscita ACS Desiderata (°C)", min_value=0.0, value=42.0)
efficienza = st.sidebar.number_input("Efficienza Scambiatore (eta)", min_value=0.1, max_value=1.0, value=0.95, step=0.001)
cp = 0.001163 # Capacità termica specifica volumetrica kWh/(l*°C)

st.sidebar.markdown("---")
st.sidebar.header("Soglie di Controllo (Isteresi)")
PdC1_Ton = st.sidebar.number_input("PdC 1 - Soglia Accensione Ton (°C)", value=50.0)
PdC1_Toff = st.sidebar.number_input("PdC 1 - Soglia Spegnimento Toff (°C)", value=55.0)
PdC1_delay = st.sidebar.number_input("PdC 1 - Delay ON (minuti)", min_value=0, value=3)

PdC2_Ton = st.sidebar.number_input("PdC 2 - Soglia Accensione Ton (°C)", value=48.0)
PdC2_Toff = st.sidebar.number_input("PdC 2 - Soglia Spegnimento Toff (°C)", value=50.0)
PdC2_delay = st.sidebar.number_input("PdC 2 - Delay ON (minuti)", min_value=0, value=6)

# ==========================================
# 2. PANNELLO CENTRALE: STRATEGIA 8 FASI
# ==========================================
st.subheader("Configurazione Strategia di Consumo (8 Fasi)")
st.write("Compila la tabella sottostante. Puoi scegliere tra controllo a 'TEMPO' (minuti) o a 'VOLUME' (litri).")

df_input_default = pd.DataFrame()
df_input_default["Nome Fase"] = [
    "Fase 1 (Vasca)", "Fase 2 (Pausa)", "Fase 3 (Doccia)", 
    "Fase 4 (Pausa)", "Fase 5 (Doccia)", "Fase 6 (Pausa)", 
    "Fase 7 (Doccia)", "Fase 8 (Mantenimento)"
]
df_input_default["Tipo Controllo"] = ["VOLUME", "TEMPO", "TEMPO", "TEMPO", "TEMPO", "TEMPO", "TEMPO", "TEMPO"]
df_input_default["Valore Limite"] = [600, 30, 10, 10, 10, 5, 10, 60]
df_input_default["Portata ACS (l/min)"] = [16, 0, 28, 0, 26, 0, 20, 0]

df_fasi = st.data_editor(
    df_input_default,
    column_config={
        "Tipo Controllo": st.column_config.SelectboxColumn(
            "Tipo Controllo",
            options=["TEMPO", "VOLUME"],
            required=True
        ),
        "Valore Limite": st.column_config.NumberColumn("Valore Limite", min_value=0),
        "Portata ACS (l/min)": st.column_config.NumberColumn("Portata ACS (l/min)", min_value=0)
    },
    disabled=["Nome Fase"], 
    hide_index=True,
    num_rows="fixed"
)

# ==========================================
# 3. MOTORE DI CALCOLO TERMODINAMICO (1 SEC)
# ==========================================
portate_secondo = list()

for idx, riga in df_fasi.iterrows():
    tipo = str(riga["Tipo Controllo"])
    limite = float(riga["Valore Limite"])
    portata_fase = float(riga["Portata ACS (l/min)"])
    
    if limite <= 0:
        continue
        
    if tipo == "TEMPO":
        secondi_fase = int(limite * 60)
        portate_secondo.extend([portata_fase] * secondi_fase)
    elif tipo == "VOLUME":
        if portata_fase > 0:
            secondi_fase = int((limite / max(0.001, portata_fase)) * 60)
            portate_secondo.extend([portata_fase] * secondi_fase)
        else:
            secondi_fase = int(limite * 60)
            portate_secondo.extend([0.0] * secondi_fase)

if len(portate_secondo) < 14400:
    differenza = 14400 - len(portate_secondo)
    portate_secondo.extend([0.0] * differenza)
else:
    portate_secondo = portate_secondo[:14400]

# Condizioni iniziali
T1 = 55.0
T2 = 55.0
T_ritorno = 0.0
litri_totali = 0.0

pdc1_stato = "SPENTA"
pdc2_stato = "SPENTA"
timer_pdc1 = 0
timer_pdc2 = 0

log_tempo = list()
log_portata = list()
log_T1 = list()
log_T2 = list()
log_T_ritorno = list()
log_litri = list()
log_T_acs = list()
log_pdc1 = list()
log_pdc2 = list()

for s in range(len(portate_secondo)):
    b_portata = portate_secondo[s]
    
    if b_portata > 0:
        t_acs_erogata = T_setpoint if (T2 - 2.0) >= T_setpoint else (T2 - 2.0)
        if t_acs_erogata < 0: t_acs_erogata = 0.0
        T_ritorno = T2 - (b_portata * (T_setpoint - T_cold)) / (efficienza * V_primario)
    else:
        t_acs_erogata = None # None interrompe la linea grafica evitando cadute a zero idilliache
        T_ritorno = 0.0
        
    # Logica Isteresi Macchine
    if pdc1_stato == "COMMUTATA su T2":
        if T1 < PdC1_Ton:
            pdc1_stato = "ACCESA"
        elif T2 >= PdC1_Toff:
            pdc1_stato = "SPENTA"
    elif pdc1_stato == "ACCESA":
        if T1 >= PdC1_Toff:
            if T2 < PdC1_Toff:
                pdc1_stato = "COMMUTATA su T2"
            else:
                pdc1_stato = "SPENTA"
    else:
        if T1 < PdC1_Ton:
            timer_pdc1 += 1
            if timer_pdc1 >= (PdC1_delay * 60):
                pdc1_stato = "ACCESA"
        else:
            timer_pdc1 = 0

    if pdc2_stato == "ACCESA":
        if T2 >= PdC2_Toff:
            pdc2_stato = "SPENTA"
            timer_pdc2 = 0
    else:
        if T2 < PdC2_Ton:
            timer_pdc2 += 1
            if timer_pdc2 >= (PdC2_delay * 60):
                pdc2_stato = "ACCESA"
        else:
            timer_pdc2 = 0

    q1_puffer1 = P1 if pdc1_stato == "ACCESA" else 0.0
    q1_puffer2 = P1 if pdc1_stato == "COMMUTATA su T2" else 0.0
    q2_puffer2 = P2 if pdc2_stato == "ACCESA" else 0.0
    
    if b_portata > 0:
        dT1 = (((V_split / 60.0) * (T_ritorno - T1) / V1) + (q1_puffer1 / (cp * V1 * 3600)))
        dT2 = (((((V_split / 60.0) * T1 + ((V_primario - V_split) / 60.0) * T_ritorno) / (V_primario / 60.0) - T2) * ((V_primario / 60.0) / V2)) + ((q2_puffer2 + q1_puffer2) / (cp * V2 * 3600)))
    else:
        dT1 = (q1_puffer1 / (cp * V1 * 3600))
        dT2 = ((q2_puffer2 + q1_puffer2) / (cp * V2 * 3600))
        
    T1 += dT1
    T2 += dT2
    litri_totali += (b_portata / 60.0)
    
    if s % 60 == 0:
        log_tempo.append(s // 60)
        log_portata.append(b_portata)
        log_T1.append(T1)
        log_T2.append(T2)
        log_T_ritorno.append(T_ritorno)
        log_litri.append(litri_totali)
        log_T_acs.append(t_acs_erogata)
        log_pdc1.append(pdc1_stato)
        log_pdc2.append(pdc2_stato)

df_risultati = pd.DataFrame({
    "Tempo (min)": log_tempo,
    "Portata ACS (l/min)": log_portata,
    "T1 - Puffer 1 (°C)": log_T1,
    "PdC 1 Stato": log_pdc1,
    "T2 - Puffer 2 (°C)": log_T2,
    "PdC 2 Stato": log_pdc2,
    "T Ritorno Scambiatore (°C)": log_T_ritorno,
    "Prelievo ACS Totale (l)": log_litri,
    "T ACS Erogata (°C)": log_T_acs
})

# ==========================================
# 4. GRAFICO INTERATTIVO PLOTLY
# ==========================================
st.markdown("---")
st.subheader("Grafico Dinamico dell'Andamento dell'Impianto")

fig = go.Figure()
fig.add_trace(go.Scatter(x=df_risultati["Tempo (min)"], y=df_risultati["T1 - Puffer 1 (°C)"], name="T1 - Puffer 1", line=dict(color='#1f77b4', width=2.5)))
fig.add_trace(go.Scatter(x=df_risultati["Tempo (min)"], y=df_risultati["T2 - Puffer 2 (°C)"], name="T2 - Puffer 2 (Mandata)", line=dict(color='#ff7f0e', width=2.5)))
fig.add_trace(go.Scatter(x=df_risultati["Tempo (min)"], y=df_risultati["T ACS Erogata (°C)"], name="T ACS Erogata", connectgaps=False, line=dict(color='#d62728', width=2.5)))
fig.add_trace(go.Scatter(x=df_risultati["Tempo (min)"], y=df_risultati["Prelievo ACS Totale (l)"], name="Volume ACS Prelevato (l)", yaxis="y2", line=dict(color='#9467bd', width=2, dash='dash')))

# Linea orizzontale di riferimento a 40°C per l'allarme di comfort
fig.add_shape(
    type="line", x0=0, x1=240, y0=40, y1=40,
    line=dict(color="DarkRed", width=1.5, dash="dashdot"),
)
# Annotazione testuale sulla linea dei 40°C
fig.add_annotation(x=10, y=40.5, text="Soglia Minima Comfort (40°C)", showarrow=False, font=dict(color="DarkRed", size=10))

fig.update_layout(
    xaxis=dict(title="Tempo (minuti)", range=[0, 180]),
    yaxis=dict(title="Temperatura (°C)", range=[20, 60]),
    yaxis2=dict(title="Volume ACS Totale Prelevato (Litri)", overlaying="y", side="right", showgrid=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    height=550,
    hovermode="x unified"
)
st.plotly_chart(fig, use_container_width=True)

# ==========================================
# 5. VISUALIZZAZIONE TABELLA DATI
# ==========================================
st.markdown("---")
st.subheader("Tabella dei Dati Minuto per Minuto")

# Funzione per convertire la tabella in un flusso di memoria Excel
def to_excel(df):
    output = io.BytesIO()
    # Riconvertiamo i None temporanei in stringhe vuote per il foglio Excel finale
    df_excel = df.copy()
    df_excel["T ACS Erogata (°C)"] = df_excel["T ACS Erogata (°C)"].fillna("-")
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_excel.to_excel(writer, index=False, sheet_name='Dati_Simulazione')
    processed_data = output.getvalue()
    return processed_data

# Generiamo il file in background
excel_data = to_excel(df_risultati)

# Inseriamo il pulsante grafico di download
st.download_button(
    label="📥 Scarica la Simulazione in formato Excel (.xlsx)",
    data=excel_data,
    file_name="Simulazione_ACS_Python.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

def evidenzia_allarme(row):
    style = [''] * len(row)
    val_t_acs = row["T ACS Erogata (°C)"]
    val_portata = row["Portata ACS (l/min)"]
    # Gestiamo il fatto che val_t_acs può essere None usando pd.isna
    if val_portata > 0 and (not pd.isna(val_t_acs)) and val_t_acs < 40.0:
        style = ['background-color: #ffccd5; color: #b70000; font-weight: bold;'] * len(row)
    return style

df_mostra = df_risultati[df_risultati["Tempo (min)"] <= 120].copy()
st.dataframe(df_mostra.style.apply(evdenzia_allarme if 'evdenzia_allarme' in locals() else evidenzia_allarme, axis=1).format({
    "Portata ACS (l/min)": "{:.0f}",
    "T1 - Puffer 1 (°C)": "{:.2f}",
    "T2 - Puffer 2 (°C)": "{:.2f}",
    "T Ritorno Scambiatore (°C)": "{:.2f}",
    "Prelievo ACS Totale (l)": "{:.0f}",
    "T ACS Erogata (°C)": lambda x: "{:.2f}".format(x) if pd.notnull(x) else "-"
}), hide_index=True, use_container_width=True)
