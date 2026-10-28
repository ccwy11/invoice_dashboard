import sqlite3
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Invoice Dashboard", layout="wide")
st.title("📊 Invoice Dashboard")

con = sqlite3.connect("invoices.db")
df = pd.read_sql("SELECT * FROM invoices ORDER BY id DESC", con)
con.close()

if df.empty:
    st.info("No invoices yet — send one to your Telegram bot!")
    st.stop()

df["total"] = pd.to_numeric(df["total"], errors="coerce")
df["date"] = pd.to_datetime(df["date"], errors="coerce")

c1, c2, c3 = st.columns(3)
c1.metric("Total invoices", len(df))
c2.metric("Total spend", f"{df['currency'].dropna().iloc[0] if df['currency'].notna().any() else ''} {df['total'].sum():,.2f}")
c3.metric("Needs review (low confidence)", int((df["confidence"] == "low").sum()))

st.subheader("Spend by category")
st.bar_chart(df.groupby("category")["total"].sum())

st.subheader("Spend by month")
st.line_chart(df.set_index("date").resample("ME")["total"].sum())

st.subheader("All invoices")
search = st.text_input("Filter by vendor / invoice #")
view = df[df["vendor"].astype(str).str.contains(search, case=False, na=False) |
          df["invoice_no"].astype(str).str.contains(search, case=False, na=False)] if search else df
st.dataframe(view, use_container_width=True)

st.download_button("⬇️ Export CSV",
    df.to_csv(index=False).encode("utf-8-sig"), "invoices.csv", "text/csv")