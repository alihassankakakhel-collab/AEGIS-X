from __future__ import annotations

from contextlib import closing
from html import escape
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st

from config import BASE_DIR, settings
from core.correlation_engine import suggest_duplicates
from core.gemini_client import GeminiClient, GeminiRequestError
from core.report_generator import generate_situation_report
from database.connection import fetch_incidents, get_connection
from database.operations import (
    create_situation_report_draft,
    fetch_activity_log,
    fetch_reports,
    fetch_report_relationships,
    fetch_report_summaries,
    fetch_resource_assignments,
    fetch_response_teams,
    fetch_simulation_scenarios,
    fetch_situation_reports,
    review_situation_report,
    seed_fictional_demo,
)
from modules.digital_twin import render_digital_twin
from modules.dispatch_queue import render_dispatch_queue
from modules.incident_manager import render_incident_manager
from modules.live_map import render_live_map
from modules.report_center import render_report_center
from modules.resource_coordination import render_resource_coordination


# ---------------------------------------------------------------------------
# Design system tokens
# ---------------------------------------------------------------------------
_BG_PRIMARY = "#080D18"
_BG_SECONDARY = "#0E1728"
_BG_CARD = "#111D30"
_ACCENT_PRIMARY = "#22D3EE"
_ACCENT_SECONDARY = "#6366F1"
_TEXT_PRIMARY = "#F8FAFC"
_TEXT_SECONDARY = "#94A3B8"
_SUCCESS = "#22C55E"
_WARNING = "#F59E0B"
_CRITICAL = "#EF4444"


def _inject_styles() -> None:
    st.markdown(
        """
        <style>
        /* ── Import Inter font ─────────────────────────────────── */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');

        /* ── Global resets ─────────────────────────────────────── */
        *, *::before, *::after { box-sizing: border-box; }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(ellipse at 10% -6%, rgba(34,211,238,.07), transparent 38%),
                radial-gradient(ellipse at 90% 110%, rgba(99,102,241,.05), transparent 40%),
                #080D18;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        }
        [data-testid="stMainBlockContainer"] {
            max-width: 1520px;
            padding-top: 2rem;
            padding-left: clamp(1rem, 3vw, 2.5rem);
            padding-right: clamp(1rem, 3vw, 2.5rem);
        }
        [data-testid="stHeader"] {
            background: rgba(8, 13, 24, .92);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0E1728 0%, #0A1220 100%);
            border-right: 1px solid rgba(148,163,184,.1);
        }
        [data-testid="stSidebarContent"] { padding-top: 1.25rem; }
        [data-testid="stSidebar"] [data-testid="stRadio"] label {
            color: #CBD5E1;
            font-weight: 600;
        }
        h1, h2, h3, h4, h5, h6 {
            font-family: 'Inter', sans-serif !important;
            letter-spacing: -.03em;
        }

        /* ── Scrollbar styling ─────────────────────────────────── */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb {
            background: rgba(148,163,184,.25);
            border-radius: 999px;
        }
        ::-webkit-scrollbar-thumb:hover { background: rgba(148,163,184,.4); }

        /* ── Metric cards ──────────────────────────────────────── */
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(17,29,48,.95), rgba(14,23,40,.95));
            border: 1px solid rgba(148,163,184,.1);
            border-radius: 14px;
            padding: 18px 20px;
            transition: border-color .25s ease, box-shadow .25s ease, transform .2s ease;
        }
        [data-testid="stMetric"]:hover {
            border-color: rgba(34,211,238,.3);
            box-shadow: 0 4px 24px rgba(34,211,238,.08);
            transform: translateY(-1px);
        }
        [data-testid="stMetric"] [data-testid="stMetricLabel"] {
            color: #94A3B8 !important;
            font-size: 11px !important;
            font-weight: 600 !important;
            letter-spacing: .06em;
            text-transform: uppercase;
        }
        [data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #F8FAFC !important;
            font-size: 28px !important;
            font-weight: 800 !important;
            letter-spacing: -.02em;
        }

        /* ── Tab styling ───────────────────────────────────────── */
        [data-testid="stTabs"] [role="tablist"] {
            gap: 2px;
            border-bottom: 1px solid rgba(148,163,184,.12);
            padding-bottom: 0;
            overflow-x: auto;
            scrollbar-width: thin;
        }
        [data-testid="stTabs"] button[role="tab"] {
            border-radius: 10px 10px 0 0;
            padding: 10px 16px;
            color: #94A3B8;
            font-weight: 600;
            font-size: 13px;
            letter-spacing: .01em;
            transition: color .2s ease, background .2s ease;
        }
        [data-testid="stTabs"] button[role="tab"]:hover {
            color: #CBD5E1;
            background: rgba(148,163,184,.06);
        }
        [data-testid="stTabs"] button[aria-selected="true"] {
            color: #67e8f9 !important;
            border-bottom: 2px solid #22D3EE;
            background: rgba(34,211,238,.04);
        }

        /* ── Form styling ──────────────────────────────────────── */
        [data-testid="stForm"] {
            border-color: rgba(148,163,184,.12);
            border-radius: 14px;
            background: rgba(17,29,48,.4);
        }

        /* ── Expander styling ──────────────────────────────────── */
        [data-testid="stExpander"] {
            border-color: rgba(148,163,184,.1);
            border-radius: 12px;
            background: rgba(17,29,48,.4);
        }

        /* ── Dataframe styling ─────────────────────────────────── */
        [data-testid="stDataFrame"] {
            border-radius: 12px;
            overflow: hidden;
        }

        /* ── Button styling ────────────────────────────────────── */
        .stButton > button {
            border-radius: 10px;
            font-weight: 600;
            letter-spacing: .01em;
            transition: all .2s ease;
        }
        .stButton > button:hover {
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(0,0,0,.2);
        }

        /* ───────────────────────────────────────────────────────── */
        /* BRAND HEADER                                            */
        /* ───────────────────────────────────────────────────────── */
        [data-testid="stHorizontalBlock"]:has(.aegis-brand-icon) {
            padding: 14px 22px;
            margin: 0 0 10px;
            border: 1px solid rgba(34,211,238,.12);
            border-radius: 16px;
            background: linear-gradient(108deg,
                rgba(14,23,40,.98) 0%,
                rgba(11,17,30,.98) 50%,
                rgba(14,23,40,.98) 100%);
            box-shadow:
                0 1px 0 0 rgba(34,211,238,.06) inset,
                0 12px 40px rgba(0,0,0,.2);
        }
        .aegis-brand-icon {
            display: grid;
            place-items: center;
            width: 44px;
            height: 44px;
            margin-right: 14px;
            border: 1px solid rgba(34,211,238,.5);
            border-radius: 12px;
            background: linear-gradient(135deg, rgba(8,145,178,.22), rgba(99,102,241,.12));
            color: #67e8f9;
            font-size: 14px;
            font-weight: 900;
            letter-spacing: .05em;
            font-family: 'Inter', sans-serif;
        }
        .aegis-brand-name {
            color: #F8FAFC;
            font-size: 20px;
            font-weight: 900;
            letter-spacing: .08em;
            line-height: 1.1;
            font-family: 'Inter', sans-serif;
        }
        .aegis-brand-name span { color: #22D3EE; }
        .aegis-brand-subtitle {
            margin-top: 3px;
            color: #94A3B8;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: .14em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
        }

        /* ── Status pills ──────────────────────────────────────── */
        .aegis-status-row {
            display: flex;
            flex-wrap: wrap;
            justify-content: center;
            gap: 6px;
        }
        .aegis-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 999px;
            background: rgba(8,13,24,.6);
            color: #CBD5E1;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: .07em;
            white-space: nowrap;
            font-family: 'Inter', sans-serif;
            transition: border-color .2s ease;
        }
        .aegis-status:hover {
            border-color: rgba(148,163,184,.25);
        }
        .aegis-status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #22C55E;
            box-shadow: 0 0 8px rgba(34,197,94,.5);
            animation: pulse-dot 2.5s ease-in-out infinite;
        }
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; box-shadow: 0 0 8px rgba(34,197,94,.5); }
            50% { opacity: .7; box-shadow: 0 0 12px rgba(34,197,94,.7); }
        }
        .aegis-status-dot.warn {
            background: #F59E0B;
            box-shadow: 0 0 8px rgba(245,158,11,.4);
            animation: pulse-warn 2.5s ease-in-out infinite;
        }
        @keyframes pulse-warn {
            0%, 100% { opacity: 1; box-shadow: 0 0 8px rgba(245,158,11,.4); }
            50% { opacity: .7; box-shadow: 0 0 12px rgba(245,158,11,.6); }
        }
        .aegis-status-dot.error {
            background: #EF4444;
            box-shadow: 0 0 8px rgba(239,68,68,.4);
        }
        .aegis-status-dot.configured {
            background: #22D3EE;
            box-shadow: 0 0 8px rgba(34,211,238,.35);
            animation: none;
        }

        /* ── Date/Time display ─────────────────────────────────── */
        .aegis-datetime {
            color: #94A3B8;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: .04em;
            text-align: right;
            font-family: 'Inter', sans-serif;
            line-height: 1.5;
        }
        .aegis-datetime .time {
            color: #CBD5E1;
            font-size: 13px;
            font-weight: 700;
        }

        /* ── Operator block ────────────────────────────────────── */
        .aegis-operator-label {
            color: #64748B;
            font-size: 9px;
            font-weight: 700;
            letter-spacing: .14em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
        }
        .aegis-operator-name {
            overflow: hidden;
            max-width: 160px;
            margin-top: 2px;
            color: #F1F5F9;
            font-size: 13px;
            font-weight: 700;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-family: 'Inter', sans-serif;
        }
        .aegis-operator-avatar {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border-radius: 10px;
            background: linear-gradient(135deg, rgba(99,102,241,.3), rgba(34,211,238,.2));
            border: 1px solid rgba(99,102,241,.3);
            color: #A5B4FC;
            font-size: 12px;
            font-weight: 800;
            margin-right: 8px;
            font-family: 'Inter', sans-serif;
        }

        /* ───────────────────────────────────────────────────────── */
        /* COMMAND CENTER PANELS                                   */
        /* ───────────────────────────────────────────────────────── */
        .cc-panel {
            background: linear-gradient(145deg,
                rgba(17,29,48,.85) 0%,
                rgba(14,23,40,.85) 100%);
            border: 1px solid rgba(148,163,184,.08);
            border-radius: 16px;
            padding: 20px 22px;
            margin-bottom: 6px;
            transition: border-color .25s ease, box-shadow .25s ease;
        }
        .cc-panel:hover {
            border-color: rgba(34,211,238,.15);
            box-shadow: 0 4px 32px rgba(0,0,0,.12);
        }
        .cc-panel-header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 16px;
        }
        .cc-panel-indicator {
            width: 4px;
            height: 28px;
            border-radius: 4px;
            flex-shrink: 0;
        }
        .cc-panel-indicator.cyan { background: #22D3EE; }
        .cc-panel-indicator.indigo { background: #6366F1; }
        .cc-panel-indicator.red { background: #EF4444; }
        .cc-panel-indicator.green { background: #22C55E; }
        .cc-panel-indicator.amber { background: #F59E0B; }
        .cc-panel-title {
            color: #F8FAFC;
            font-size: 15px;
            font-weight: 700;
            letter-spacing: -.01em;
            font-family: 'Inter', sans-serif;
        }
        .cc-panel-subtitle {
            color: #64748B;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: .05em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
        }

        /* ── KPI cards ─────────────────────────────────────────── */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 12px;
            margin-bottom: 8px;
        }
        @media (max-width: 1200px) {
            .kpi-grid { grid-template-columns: repeat(3, 1fr); }
        }
        @media (max-width: 768px) {
            .kpi-grid { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 480px) {
            .kpi-grid { grid-template-columns: 1fr; }
        }
        .kpi-card {
            background: linear-gradient(145deg,
                rgba(17,29,48,.9) 0%,
                rgba(14,23,40,.9) 100%);
            border: 1px solid rgba(148,163,184,.08);
            border-radius: 14px;
            padding: 18px 18px 16px;
            transition: all .25s ease;
            position: relative;
            overflow: hidden;
        }
        .kpi-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 2px;
            border-radius: 14px 14px 0 0;
            opacity: 0;
            transition: opacity .25s ease;
        }
        .kpi-card:hover {
            border-color: rgba(148,163,184,.18);
            transform: translateY(-2px);
            box-shadow: 0 8px 28px rgba(0,0,0,.15);
        }
        .kpi-card:hover::before { opacity: 1; }
        .kpi-card.cyan::before { background: linear-gradient(90deg, #22D3EE, transparent); }
        .kpi-card.indigo::before { background: linear-gradient(90deg, #6366F1, transparent); }
        .kpi-card.red::before { background: linear-gradient(90deg, #EF4444, transparent); }
        .kpi-card.amber::before { background: linear-gradient(90deg, #F59E0B, transparent); }
        .kpi-card.green::before { background: linear-gradient(90deg, #22C55E, transparent); }
        .kpi-tag {
            display: inline-block;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
            padding: 2px 8px;
            border-radius: 6px;
            margin-bottom: 10px;
            font-family: 'Inter', sans-serif;
        }
        .kpi-tag.cyan { background: rgba(34,211,238,.1); color: #67E8F9; border: 1px solid rgba(34,211,238,.2); }
        .kpi-tag.indigo { background: rgba(99,102,241,.1); color: #A5B4FC; border: 1px solid rgba(99,102,241,.2); }
        .kpi-tag.red { background: rgba(239,68,68,.08); color: #FCA5A5; border: 1px solid rgba(239,68,68,.15); }
        .kpi-tag.amber { background: rgba(245,158,11,.08); color: #FCD34D; border: 1px solid rgba(245,158,11,.15); }
        .kpi-tag.green { background: rgba(34,197,94,.08); color: #86EFAC; border: 1px solid rgba(34,197,94,.15); }
        .kpi-value {
            color: #F8FAFC;
            font-size: 30px;
            font-weight: 800;
            letter-spacing: -.03em;
            line-height: 1;
            margin-bottom: 4px;
            font-family: 'Inter', sans-serif;
        }
        .kpi-icon {
            display: grid;
            place-items: center;
            width: 30px;
            height: 30px;
            margin-bottom: 10px;
            border: 1px solid currentColor;
            border-radius: 9px;
            opacity: .9;
        }
        .kpi-icon svg { width: 16px; height: 16px; }
        .kpi-icon.cyan { color: #67E8F9; }
        .kpi-icon.indigo { color: #A5B4FC; }
        .kpi-icon.red { color: #FCA5A5; }
        .kpi-icon.amber { color: #FCD34D; }
        .kpi-icon.green { color: #86EFAC; }
        .kpi-label {
            color: #94A3B8;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: .04em;
            text-transform: uppercase;
            margin-bottom: 4px;
            font-family: 'Inter', sans-serif;
        }
        .kpi-desc {
            color: #64748B;
            font-size: 10px;
            font-weight: 500;
            line-height: 1.4;
            font-family: 'Inter', sans-serif;
        }

        /* ── Priority incident cards ───────────────────────────── */
        .incident-card {
            background: rgba(14,23,40,.6);
            border: 1px solid rgba(148,163,184,.08);
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 8px;
            transition: all .2s ease;
            display: flex;
            align-items: flex-start;
            gap: 12px;
        }
        .incident-card:hover {
            border-color: rgba(148,163,184,.18);
            background: rgba(17,29,48,.6);
        }
        .incident-card.border-critical { border-left: 3px solid #EF4444; }
        .incident-card.border-high { border-left: 3px solid #F59E0B; }
        .incident-card.border-medium { border-left: 3px solid #6366F1; }
        .incident-card.border-low { border-left: 3px solid #22C55E; }
        .incident-severity {
            display: inline-flex;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 800;
            letter-spacing: .06em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
            white-space: nowrap;
        }
        .severity-critical {
            background: rgba(239,68,68,.12);
            color: #FCA5A5;
            border: 1px solid rgba(239,68,68,.2);
        }
        .severity-high {
            background: rgba(245,158,11,.1);
            color: #FCD34D;
            border: 1px solid rgba(245,158,11,.18);
        }
        .severity-medium {
            background: rgba(99,102,241,.1);
            color: #A5B4FC;
            border: 1px solid rgba(99,102,241,.18);
        }
        .severity-low {
            background: rgba(34,197,94,.08);
            color: #86EFAC;
            border: 1px solid rgba(34,197,94,.15);
        }
        .incident-id {
            color: #64748B;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: .05em;
            font-family: 'Inter', sans-serif;
        }
        .incident-title {
            color: #E2E8F0;
            font-size: 13px;
            font-weight: 600;
            line-height: 1.4;
            margin: 3px 0;
            font-family: 'Inter', sans-serif;
        }
        .incident-meta {
            color: #64748B;
            font-size: 10px;
            font-weight: 500;
            font-family: 'Inter', sans-serif;
        }
        .incident-meta span {
            color: #94A3B8;
        }
        .incident-score-box {
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 8px 12px;
            border-radius: 10px;
            background: rgba(8,13,24,.5);
            border: 1px solid rgba(148,163,184,.08);
            flex-shrink: 0;
        }
        .incident-score-label {
            color: #64748B;
            font-size: 8px;
            font-weight: 700;
            letter-spacing: .1em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
        }
        .incident-score-value {
            color: #F8FAFC;
            font-size: 20px;
            font-weight: 800;
            font-family: 'Inter', sans-serif;
        }

        /* ── Activity feed ─────────────────────────────────────── */
        .activity-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 10px 0;
            border-bottom: 1px solid rgba(148,163,184,.06);
        }
        .activity-item:last-child { border-bottom: none; }
        .activity-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            margin-top: 5px;
            flex-shrink: 0;
        }
        .activity-dot.ai { background: #22D3EE; }
        .activity-dot.operator { background: #6366F1; }
        .activity-dot.system { background: #94A3B8; }
        .activity-text {
            color: #CBD5E1;
            font-size: 12px;
            font-weight: 500;
            line-height: 1.5;
            font-family: 'Inter', sans-serif;
        }
        .activity-time {
            color: #475569;
            font-size: 10px;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
            white-space: nowrap;
        }
        .activity-actor {
            color: #94A3B8;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: .03em;
            font-family: 'Inter', sans-serif;
        }

        /* ── Correlation overview ──────────────────────────────── */
        .corr-stat {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 14px;
            border-radius: 10px;
            background: rgba(8,13,24,.4);
            border: 1px solid rgba(148,163,184,.06);
            margin-bottom: 6px;
            transition: border-color .2s ease;
        }
        .corr-stat:hover {
            border-color: rgba(148,163,184,.14);
        }
        .corr-stat-value {
            color: #F8FAFC;
            font-size: 22px;
            font-weight: 800;
            font-family: 'Inter', sans-serif;
            min-width: 36px;
        }
        .corr-stat-label {
            color: #94A3B8;
            font-size: 11px;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
        }

        /* ── Response team cards ───────────────────────────────── */
        .team-card {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 14px;
            border-radius: 10px;
            background: rgba(14,23,40,.5);
            border: 1px solid rgba(148,163,184,.06);
            margin-bottom: 6px;
        }
        .team-status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            flex-shrink: 0;
        }
        .team-status-dot.available { background: #22C55E; }
        .team-status-dot.assigned { background: #F59E0B; }
        .team-name {
            color: #E2E8F0;
            font-size: 12px;
            font-weight: 600;
            font-family: 'Inter', sans-serif;
        }
        .team-base {
            color: #64748B;
            font-size: 10px;
            font-weight: 500;
            font-family: 'Inter', sans-serif;
        }
        .team-badge {
            margin-left: auto;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 9px;
            font-weight: 700;
            letter-spacing: .05em;
            text-transform: uppercase;
            font-family: 'Inter', sans-serif;
        }
        .team-badge.available {
            background: rgba(34,197,94,.1);
            color: #86EFAC;
            border: 1px solid rgba(34,197,94,.18);
        }
        .team-badge.assigned {
            background: rgba(245,158,11,.1);
            color: #FCD34D;
            border: 1px solid rgba(245,158,11,.18);
        }

        /* ── Section divider ───────────────────────────────────── */
        .section-divider {
            height: 1px;
            background: linear-gradient(90deg,
                transparent, rgba(148,163,184,.12) 20%,
                rgba(148,163,184,.12) 80%, transparent);
            margin: 10px 0;
        }

        /* ── Overview page title area ──────────────────────────── */
        .cc-title-area {
            margin-bottom: 4px;
        }
        .cc-title {
            color: #F8FAFC;
            font-size: 26px;
            font-weight: 800;
            letter-spacing: -.03em;
            font-family: 'Inter', sans-serif;
            margin-bottom: 2px;
        }
        .cc-tagline {
            color: #64748B;
            font-size: 12px;
            font-weight: 500;
            letter-spacing: .02em;
            font-family: 'Inter', sans-serif;
        }
        .cc-tagline em {
            color: #94A3B8;
            font-style: normal;
        }

        /* ── Disclaimer bar ────────────────────────────────────── */
        .cc-disclaimer {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 14px;
            border-radius: 10px;
            background: rgba(99,102,241,.06);
            border: 1px solid rgba(99,102,241,.12);
            margin-bottom: 12px;
        }
        .cc-disclaimer-indicator {
            width: 3px;
            height: 18px;
            border-radius: 2px;
            background: #6366F1;
            flex-shrink: 0;
        }
        .cc-disclaimer-text {
            color: #94A3B8;
            font-size: 11px;
            font-weight: 500;
            font-family: 'Inter', sans-serif;
            line-height: 1.4;
        }

        /* ── Empty state ───────────────────────────────────────── */
        .cc-empty-state {
            text-align: center;
            padding: 24px 20px;
            color: #64748B;
            font-size: 13px;
            font-weight: 500;
            font-family: 'Inter', sans-serif;
        }
        .relationship-diagram {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(92px, .75fr) minmax(0, 1fr);
            align-items: center;
            gap: 10px;
            margin: 12px 0 16px;
        }
        .relationship-node {
            min-width: 0;
            padding: 10px 12px;
            border: 1px solid rgba(148,163,184,.12);
            border-radius: 10px;
            background: rgba(8,13,24,.46);
        }
        .relationship-node small,
        .relationship-link small {
            display: block;
            color: #64748B;
            font-size: 9px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
        }
        .relationship-node strong {
            display: block;
            overflow: hidden;
            margin-top: 3px;
            color: #CBD5E1;
            font-size: 11px;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .relationship-link { text-align: center; }
        .relationship-link span {
            display: block;
            color: #22D3EE;
            font-size: 19px;
            line-height: 1.1;
        }
        .relationship-link.confirmed span { color: #22C55E; }
        .relationship-link.pending span { color: #F59E0B; }
        @media (max-width: 900px) {
            .aegis-status-row { justify-content: flex-start; }
            .aegis-datetime { text-align: left; }
            .cc-panel { padding: 16px; }
        }
        @media (max-width: 520px) {
            .kpi-grid { gap: 8px; }
            .kpi-card { padding: 14px; }
            .kpi-value { font-size: 25px; }
            .relationship-diagram {
                grid-template-columns: minmax(0, 1fr);
                gap: 6px;
            }
            .relationship-link span { transform: rotate(90deg); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _navigate_dashboard_tab(tab_name: str) -> None:
    st.session_state["dashboard-navigation"] = tab_name


def _open_incident_details(incident_id: int) -> None:
    st.session_state["cc-selected-incident-id"] = incident_id


def _clear_incident_details() -> None:
    st.session_state.pop("cc-selected-incident-id", None)


def _render_top_navigation(needs_review_count: int) -> None:
    _inject_styles()
    operator = escape(st.session_state.get("operator_name", "Operator"))
    operator_initials = "".join(
        word[0].upper() for word in operator.split()[:2]
    ) or "OP"
    api_key_ready = bool(settings.gemini_api_key)
    try:
        with closing(get_connection(settings.database_path)) as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        database_ready = False
    else:
        database_ready = True
    gemini_class = " configured" if api_key_ready else " warn"
    db_class = "" if database_ready else " error"
    system_class = "" if database_ready else " error"

    now = datetime.now()
    date_str = now.strftime("%A, %B %d, %Y")
    time_str = now.strftime("%H:%M")

    brand, status, datetime_col, notifications_col, operator_column = st.columns(
        [1.8, 3.8, 1.3, 1.4, 1.7],
        vertical_alignment="center",
    )
    with brand:
        st.markdown(
            """
            <div style="display:flex;align-items:center">
              <div class="aegis-brand-icon">AX</div>
              <div>
                <div class="aegis-brand-name">AEGIS <span>X</span></div>
                <div class="aegis-brand-subtitle">Command Center</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with status:
        st.markdown(
            f"""
            <div class="aegis-status-row">
              <div class="aegis-status"><span class="aegis-status-dot{db_class}"></span>
                DATABASE {"CONNECTED" if database_ready else "OFFLINE"}</div>
              <div class="aegis-status"><span class="aegis-status-dot{gemini_class}"></span>
                GEMINI AI {"CONFIGURED" if api_key_ready else "NOT CONFIGURED"}</div>
              <div class="aegis-status" title="Application process is serving this dashboard">
                <span class="aegis-status-dot{system_class}"></span>
                SYSTEM {"READY" if database_ready else "DEGRADED"}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with datetime_col:
        st.markdown(
            f"""
            <div class="aegis-datetime">
              <div class="time">{time_str}</div>
              <div>{date_str}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with notifications_col:
        st.button(
            f"Needs review · {needs_review_count}",
            key="cc-notifications",
            help="Open incidents awaiting operator verification.",
            on_click=_navigate_dashboard_tab,
            args=("Incidents",),
            use_container_width=True,
        )
    with operator_column:
        operator_info, logout_column = st.columns(
            [1.6, 1],
            vertical_alignment="center",
        )
        with operator_info:
            st.markdown(
                f'<div style="display:flex;align-items:center">'
                f'<div class="aegis-operator-avatar">{operator_initials}</div>'
                f'<div>'
                f'<div class="aegis-operator-label">Operator</div>'
                f'<div class="aegis-operator-name">{operator}</div>'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        with logout_column:
            if st.button("Sign out", key="top-navigation-sign-out"):
                st.session_state.pop("operator_authenticated", None)
                st.session_state.pop("operator_name", None)
                st.rerun()


def _priority_rank(incident: dict) -> tuple[int, int]:
    rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    return (
        rank.get(incident["severity"], 4),
        -int(incident.get("priority_score") or 0),
    )


def _severity_class(severity: str) -> str:
    return {
        "Critical": "critical",
        "High": "high",
        "Medium": "medium",
        "Low": "low",
    }.get(severity, "medium")


def _format_time_ago(timestamp_str: str | None) -> str:
    """Return a human-readable relative time string."""
    if not timestamp_str:
        return "—"
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m}m ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        d = seconds // 86400
        return f"{d}d ago"
    except (ValueError, TypeError):
        return timestamp_str[:16] if len(timestamp_str) > 16 else timestamp_str


def _activity_dot_class(action: str) -> str:
    """Classify an activity log entry for dot coloring."""
    action_lower = action.lower()
    if "analysis" in action_lower or "incident created from report" in action_lower:
        return "ai"
    if "review" in action_lower or "verified" in action_lower:
        return "operator"
    return "system"


def _render_kpi_cards(
    total_reports: int,
    active_incidents: int,
    critical_count: int,
    unverified_count: int,
    confirmed_relationships: int,
) -> None:
    """Render five premium KPI statistic cards without emoji stickers."""
    st.markdown(
        f"""
        <div class="kpi-grid">
          <div class="kpi-card cyan">
            <div class="kpi-icon cyan" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M7 3.75h7l4 4v12.5H7a2 2 0 0 1-2-2v-12.5a2 2 0 0 1 2-2Z"/>
                <path d="M14 3.75v4h4M8.5 12h7M8.5 15.5h7"/>
              </svg>
            </div>
            <div class="kpi-value">{total_reports}</div>
            <div class="kpi-label">Total Reports</div>
            <div class="kpi-desc">All emergency reports stored in the database</div>
          </div>
          <div class="kpi-card indigo">
            <div class="kpi-icon indigo" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M3.5 12h4l2.2-5 4.1 10 2.2-5h4.5"/>
                <circle cx="12" cy="12" r="9"/>
              </svg>
            </div>
            <div class="kpi-value">{active_incidents}</div>
            <div class="kpi-label">Active Incidents</div>
            <div class="kpi-desc">Open incidents requiring active monitoring</div>
          </div>
          <div class="kpi-card red">
            <div class="kpi-icon red" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="m12 3.5 9 16H3l9-16Z"/>
                <path d="M12 9v4.5M12 16.5h.01"/>
              </svg>
            </div>
            <div class="kpi-value">{critical_count}</div>
            <div class="kpi-label">Critical Priority</div>
            <div class="kpi-desc">Open incidents marked critical priority</div>
          </div>
          <div class="kpi-card amber">
            <div class="kpi-icon amber" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <circle cx="12" cy="12" r="9"/>
                <path d="M12 7v5l3 2"/>
              </svg>
            </div>
            <div class="kpi-value">{unverified_count}</div>
            <div class="kpi-label">Pending Verification</div>
              <div class="kpi-desc">Incidents awaiting operator review</div>
          </div>
          <div class="kpi-card green">
            <div class="kpi-icon green" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <circle cx="6" cy="12" r="2.5"/>
                <circle cx="18" cy="6" r="2.5"/>
                <circle cx="18" cy="18" r="2.5"/>
                <path d="m8.2 10.9 7.5-3.8M8.2 13.1l7.5 3.8"/>
              </svg>
            </div>
            <div class="kpi-value">{confirmed_relationships}</div>
            <div class="kpi-label">Confirmed Relations</div>
            <div class="kpi-desc">Operator-verified report correlations</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_priority_incidents_panel(
    alerts: list[dict],
    database_path,
) -> None:
    """Render the Priority Incidents monitoring panel."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator red"></div>
          <div>
            <div class="cc-panel-title">Priority Incidents</div>
            <div class="cc-panel-subtitle">Active Critical & High Priority Monitoring</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not alerts:
        st.markdown(
            '<div class="cc-empty-state">'
            'No open critical or high-priority incidents at this time.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    for incident in alerts:
        sev = incident["severity"]
        sev_cls = _severity_class(sev)
        simulated = bool(incident.get("demo_key"))
        sim_tag = '<span style="color:#F59E0B;font-size:9px;font-weight:700;letter-spacing:.06em">SIMULATED</span> · ' if simulated else ''
        category = escape(incident.get("category") or "Unclassified")
        location = escape(incident.get("location") or "Location not reported")
        status = escape(incident["status"])
        time_ago = _format_time_ago(incident.get("reported_at") or incident.get("timestamp"))
        score = incident.get("priority_score") or 0
        desc = escape(incident["description"][:120])
        report_count = int(incident.get("report_id") is not None)

        st.markdown(
            f'<div class="incident-card border-{sev_cls}">'
            f'  <div style="flex:1;min-width:0">'
            f'    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:4px">'
            f'      <span class="incident-severity severity-{sev_cls}">{escape(sev)}</span>'
            f'      <span class="incident-id">INC-{incident["id"]:04d}</span>'
            f'      <span class="incident-id">{sim_tag}{escape(category)}</span>'
            f'    </div>'
            f'    <div class="incident-title">{desc}</div>'
            f'    <div class="incident-meta">'
            f'      Location: <span>{location}</span> · Reported: <span>{time_ago}</span> · '
            f'      Status: <span>{escape(status)}</span> · '
            f'      Reports: <span>{report_count}</span>'
            f'    </div>'
            f'  </div>'
            f'  <div class="incident-score-box">'
            f'    <div class="incident-score-label">Score</div>'
            f'    <div class="incident-score-value">{score}</div>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.button(
            "View incident",
            key=f"cc-view-incident-{incident['id']}",
            on_click=_open_incident_details,
            args=(incident["id"],),
        )
        if st.session_state.get("cc-selected-incident-id") == incident["id"]:
            with st.expander(
                f"Incident INC-{incident['id']:04d} details",
                expanded=True,
            ):
                st.write(incident["description"])
                detail_columns = st.columns(3)
                detail_columns[0].caption(f"Category: {category}")
                detail_columns[1].caption(f"Priority: {sev}")
                detail_columns[2].caption(f"Status: {status}")
                st.caption(
                    f"Reported: {incident.get('reported_at') or incident.get('timestamp') or 'Not recorded'}"
                )
                if incident.get("report_id"):
                    st.caption(f"Associated report: {incident['report_id']}")
                if incident.get("priority_explanation"):
                    st.caption(
                        f"Priority rationale: {incident['priority_explanation']}"
                    )
                st.button(
                    "Close details",
                    key=f"cc-close-incident-{incident['id']}",
                    on_click=_clear_incident_details,
                )


def _render_activity_panel(database_path) -> None:
    """Render AI Intelligence Activity panel from real activity logs."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator cyan"></div>
          <div>
            <div class="cc-panel-title">AI Intelligence Activity</div>
            <div class="cc-panel-subtitle">Recent AI and operator activity · local audit log</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    entries = fetch_activity_log(database_path, limit=12)
    if not entries:
        st.markdown(
            '<div class="cc-empty-state">'
            'No intelligence activity recorded yet. Submit a report to begin.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    items_html = ""
    for entry in entries[:10]:
        action = escape(entry["action"])
        actor = escape(entry["actor"])
        time_str = _format_time_ago(entry["created_at"])
        dot_cls = _activity_dot_class(entry["action"])

        ref_parts = []
        if entry.get("report_id"):
            ref_parts.append(f'Report {escape(str(entry["report_id"])[:8])}…')
        if entry.get("incident_id"):
            ref_parts.append(f'Incident #{entry["incident_id"]}')
        ref_text = " · ".join(ref_parts)
        ref_html = f'<div class="incident-id" style="margin-top:2px">{ref_text}</div>' if ref_text else ""

        items_html += (
            f'<div class="activity-item">'
            f'  <div class="activity-dot {dot_cls}"></div>'
            f'  <div style="flex:1;min-width:0">'
            f'    <div class="activity-text">{action}</div>'
            f'    <div style="display:flex;gap:8px;align-items:center;margin-top:2px">'
            f'      <span class="activity-actor">{actor}</span>'
            f'      <span class="activity-time">{time_str}</span>'
            f'    </div>'
            f'    {ref_html}'
            f'  </div>'
            f'</div>'
        )
    st.markdown(items_html, unsafe_allow_html=True)


def _render_correlation_panel(database_path) -> None:
    """Render the Incident Correlation Overview panel."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator indigo"></div>
          <div>
            <div class="cc-panel-title">Incident Correlation Engine</div>
            <div class="cc-panel-subtitle">Report Relationship Intelligence</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    reports = fetch_reports(database_path)
    total_reports = len(reports)
    analyzed_reports = sum(1 for r in reports if r["analysis_status"] == "Complete")
    relationships = fetch_report_relationships(database_path)
    confirmed = sum(1 for s in relationships.values() if s == "Confirmed related")
    summaries = fetch_report_summaries(database_path)
    suggested_pairs: set[tuple[str, str]] = set()
    for candidate in summaries:
        others = [report for report in summaries if report.report_id != candidate.report_id]
        for suggestion in suggest_duplicates(candidate, others):
            suggested_pairs.add(
                tuple(sorted((candidate.report_id, suggestion.report_id)))
            )
    pending_pairs = suggested_pairs.difference(relationships)
    confirmed_pairs = [
        pair
        for pair, status in relationships.items()
        if status == "Confirmed related"
    ]

    diagram_pairs = [
        *((pair, "confirmed") for pair in sorted(confirmed_pairs)[:2]),
        *((pair, "pending") for pair in sorted(pending_pairs)[:2]),
    ]
    diagram_html = ""
    for pair, relation_status in diagram_pairs:
        status_label = (
            "Operator confirmed" if relation_status == "confirmed"
            else "AI suggestion · review needed"
        )
        diagram_html += (
            '<div class="relationship-node">'
            f'<small>Report</small><strong>{escape(pair[0][:12])}…</strong>'
            '</div>'
            f'<div class="relationship-link {relation_status}">'
            '<span>↔</span>'
            f'<small>{escape(status_label)}</small>'
            '</div>'
            '<div class="relationship-node">'
            f'<small>Report</small><strong>{escape(pair[1][:12])}…</strong>'
            '</div>'
        )
    if diagram_html:
        st.markdown(
            f'<div class="relationship-diagram">{diagram_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("No confirmed or suggested report relationships are recorded.")

    st.markdown(
        f"""
        <div class="corr-stat">
          <div class="corr-stat-value">{total_reports}</div>
          <div class="corr-stat-label">Total reports submitted to system</div>
        </div>
        <div class="corr-stat">
          <div class="corr-stat-value">{analyzed_reports}</div>
          <div class="corr-stat-label">Reports analyzed by AI engine</div>
        </div>
        <div class="corr-stat">
          <div class="corr-stat-value">{len(suggested_pairs)}</div>
          <div class="corr-stat-label">Potential duplicate pairs detected</div>
        </div>
        <div class="corr-stat">
          <div class="corr-stat-value" style="color:#22C55E">{confirmed}</div>
          <div class="corr-stat-label">Confirmed related relationships</div>
        </div>
        <div class="corr-stat">
          <div class="corr-stat-value" style="color:#F59E0B">{len(pending_pairs)}</div>
          <div class="corr-stat-label">Relationships awaiting operator review</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_response_coordination_panel(database_path) -> None:
    """Render Response Coordination summary panel."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator green"></div>
          <div>
            <div class="cc-panel-title">Response Coordination</div>
            <div class="cc-panel-subtitle">Simulated Team Deployment Status</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    teams = fetch_response_teams(database_path)
    assignments = fetch_resource_assignments(database_path)
    available_count = sum(1 for t in teams if t["status"] == "Available")
    assigned_count = sum(1 for t in teams if t["status"] == "Assigned")
    active_assignments = sum(1 for a in assignments if a["status"] == "Active")

    if not teams:
        st.markdown(
            '<div class="cc-empty-state">'
            'No response teams registered. Use the Response tab to add teams.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Summary metrics
    team_metrics = st.columns(3)
    team_metrics[0].metric("Available", available_count)
    team_metrics[1].metric("Assigned", assigned_count)
    team_metrics[2].metric("Active Tasks", active_assignments)

    # Team list
    for team in teams[:6]:
        status_cls = "available" if team["status"] == "Available" else "assigned"
        st.markdown(
            f'<div class="team-card">'
            f'  <div class="team-status-dot {status_cls}"></div>'
            f'  <div style="flex:1;min-width:0">'
            f'    <div class="team-name">{escape(team["name"])}</div>'
            f'    <div class="team-base">Base: {escape(team["base_location"])}</div>'
            f'  </div>'
            f'  <div class="team-badge {status_cls}">{escape(team["status"])}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    st.caption(
        "Simulation only — assignments do not dispatch or track real responders."
    )


def _render_geographical_panel(incidents: list[dict], database_path: Path) -> None:
    """Render a reliable geographical overview using standard OpenStreetMap tiles."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator amber"></div>
          <div>
            <div class="cc-panel-title">Geographical Intelligence</div>
            <div class="cc-panel-subtitle">Peshawar Incident Distribution</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    mapped = [
        incident
        for incident in incidents
        if incident.get("latitude") is not None
        and incident.get("longitude") is not None
    ]
    active_scenarios = [
        scenario
        for scenario in fetch_simulation_scenarios(database_path)
        if scenario["active"]
    ]
    categories: dict[str, int] = {}
    for inc in incidents:
        cat = inc.get("category") or "Unclassified"
        categories[cat] = categories.get(cat, 0) + 1

    geo_metrics = st.columns(3)
    geo_metrics[0].metric("Mapped Incidents", len(mapped))
    geo_metrics[1].metric("Total Incidents", len(incidents))
    geo_metrics[2].metric("Active Simulations", len(active_scenarios))

    if mapped or active_scenarios:
        import folium
        from streamlit_folium import st_folium

        city_map = folium.Map(
            location=(34.0123846, 71.5787458),
            zoom_start=12,
            control_scale=True,
        )

        severity_colors = {
            "Critical": "#EF4444",
            "High": "#F59E0B",
            "Medium": "#6366F1",
            "Low": "#22C55E",
        }

        for inc in mapped:
            simulated = bool(inc.get("demo_key"))
            sev = inc.get("severity", "Medium")
            color = severity_colors.get(sev, "#94A3B8")
            sim_prefix = "SIMULATED · " if simulated else ""
            category = inc.get("category") or "Unclassified"

            folium.CircleMarker(
                location=(inc["latitude"], inc["longitude"]),
                radius=8 if sev == "Critical" else 6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.75,
                weight=2,
                tooltip=escape(
                    f"{sim_prefix}INC-{inc['id']:04d}: "
                    f"{category} ({sev})"
                ),
                popup=folium.Popup(
                    f"<b>{escape(sim_prefix)}{escape(sev)}</b> — "
                    f"{escape(category)}<br>"
                    f"{escape(inc['description'][:100])}",
                    max_width=280,
                ),
            ).add_to(city_map)

        scenario_colors = {
            "Road closure": "#F59E0B",
            "Flood zone": "#22D3EE",
        }
        for scenario in active_scenarios:
            scenario_color = scenario_colors[scenario["scenario_type"]]
            scenario_description = (
                "hypothetical road-closure area"
                if scenario["scenario_type"] == "Road closure"
                else "illustrative flood-zone area"
            )
            folium.Circle(
                location=(scenario["latitude"], scenario["longitude"]),
                radius=scenario["radius_m"],
                color=scenario_color,
                fill=True,
                fill_color=scenario_color,
                fill_opacity=0.14,
                weight=2,
                tooltip=escape(
                    f"SIMULATION ONLY — {scenario['name']} · {scenario_description}"
                ),
                popup=folium.Popup(
                    f"<b>SIMULATION ONLY — {escape(scenario['name'])}</b><br>"
                    f"{escape(scenario_description)}; "
                    f"approximately {scenario['radius_m']} m radius. "
                    "Not a live road or flood report.",
                    max_width=300,
                ),
            ).add_to(city_map)

        st_folium(
            city_map,
            use_container_width=True,
            height=360,
            key="cc-geo-map",
        )

        if categories:
            legend_parts = []
            for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:6]:
                legend_parts.append(
                    f"<span style='color:#94A3B8;font-size:11px'>"
                    f"{escape(cat)}: <b style='color:#CBD5E1'>{count}</b></span>"
                )
            st.markdown(
                f'<div style="display:flex;flex-wrap:wrap;gap:12px;margin-top:4px">{"".join(legend_parts)}</div>',
                unsafe_allow_html=True,
            )
        if active_scenarios:
            st.caption(
                "Amber and cyan circles are operator-created simulations only; "
                "their radii are illustrative, not mapped road closures or observed flooding."
            )
    else:
        st.info(
            "No incidents have explicit coordinates and no simulated areas are active. "
            "No event locations are inferred."
        )

    st.caption(
        "Coordinates are unverified; demo markers are road-reference points. "
        "Map data: © OpenStreetMap contributors."
    )


def _render_recent_reports_panel(database_path, incidents: list[dict]) -> None:
    """Render the Recent Reports table with search capability."""
    st.markdown(
        """
        <div class="cc-panel-header">
          <div class="cc-panel-indicator cyan"></div>
          <div>
            <div class="cc-panel-title">Recent Reports</div>
            <div class="cc-panel-subtitle">Latest Submitted Emergency Intelligence</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    reports = fetch_reports(database_path)
    if not reports:
        st.markdown(
            '<div class="cc-empty-state">'
            'No reports submitted yet. Use the Intelligence tab to submit reports.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Build incident lookup for report-to-incident mapping
    incident_map = {}
    for inc in incidents:
        if inc.get("report_id"):
            incident_map[inc["report_id"]] = inc

    search_column, status_column = st.columns([2, 1])
    with search_column:
        search = st.text_input(
            "Search reports",
            placeholder="Search report ID, type, location, status, or report text...",
            key="cc-report-search",
        )
    with status_column:
        analysis_filter = st.selectbox(
            "Analysis status",
            ["All", *dict.fromkeys(report["analysis_status"] for report in reports)],
            key="cc-report-analysis-filter",
        )

    rows = []
    for report in reports:
        if (
            analysis_filter != "All"
            and report["analysis_status"] != analysis_filter
        ):
            continue
        inc = incident_map.get(report["id"])
        row = {
            "Report ID": report["id"][:12] + "…" if len(report["id"]) > 12 else report["id"],
            "Report Type": report["source_type"],
            "Location": (
                inc.get("location") or "Not reported"
                if inc else "Not analyzed"
            ),
            "Submitted": report["created_at"][:16] if report["created_at"] else "—",
            "Analysis Status": report["analysis_status"],
            "Related Incident": f'INC-{inc["id"]:04d}' if inc else "—",
            "Verification": (
                "Operator verified"
                if inc and inc["status"] == "Verified"
                else "Unverified" if inc else "Not available"
            ),
        }
        if search:
            search_lower = search.lower()
            searchable = " ".join(str(v).lower() for v in row.values())
            searchable += " " + (report.get("original_text") or "").lower()
            if search_lower not in searchable:
                continue
        rows.append(row)

    if rows:
        st.caption(f"Showing the {min(len(rows), 20)} most recent matching reports.")
        st.dataframe(rows[:20], width="stretch", hide_index=True)
    else:
        st.info("No reports match the selected search and analysis filters.")


def _render_command_center(
    database_path: Path,
    incidents: list[dict] | None = None,
) -> None:
    # ── Disclaimer and demo loader ─────────────────────────────
    st.markdown(
        '<div class="cc-disclaimer">'
        '<div class="cc-disclaimer-indicator"></div>'
        '<div class="cc-disclaimer-text">'
        'Decision support only — verify all reports and AI output. '
        'Demo records are fictional; map points are approximate road references. '
        'People control every response decision.'
        '</div></div>',
        unsafe_allow_html=True,
    )

    if st.button("Load Fictional Peshawar Demo Scenario", type="secondary"):
        try:
            added = seed_fictional_demo(
                database_path,
                BASE_DIR / "sample_data" / "fictional_peshawar_reports.csv",
            )
        except (OSError, ValueError, sqlite3.Error) as error:
            st.error(f"Could not load the fictional scenario: {error}")
        else:
            st.success(f"Added {added} fictional reports to the system.")
            st.rerun()

    # ── Fetch data ─────────────────────────────────────────────
    if incidents is None:
        incidents = fetch_incidents(database_path)
    reports = fetch_reports(database_path)
    open_incidents = [
        item for item in incidents if item["status"] not in {"Resolved", "Closed"}
    ]
    critical_count = sum(
        item["severity"] == "Critical" for item in open_incidents
    )
    unverified_count = sum(
        item["status"] in {"New", "Under Review"} for item in incidents
    )
    relationships = fetch_report_relationships(database_path)
    confirmed_relations = sum(
        1 for s in relationships.values() if s == "Confirmed related"
    )

    # ── KPI Cards ──────────────────────────────────────────────
    _render_kpi_cards(
        total_reports=len(reports),
        active_incidents=len(open_incidents),
        critical_count=critical_count,
        unverified_count=unverified_count,
        confirmed_relationships=confirmed_relations,
    )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Priority Incidents + AI Activity (two-column) ──────────
    priority_col, activity_col = st.columns([1.3, 1])

    with priority_col:
        alerts = sorted(
            [
                item
                for item in open_incidents
                if item["severity"] in {"Critical", "High"}
            ],
            key=_priority_rank,
        )[:6]
        _render_priority_incidents_panel(alerts, database_path)

        st.button(
            "View all incidents →",
            key="cc-view-all-incidents",
            on_click=_navigate_dashboard_tab,
            args=("Incidents",),
        )

    with activity_col:
        _render_activity_panel(database_path)

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Correlation Overview + Response Coordination ───────────
    corr_col, response_col = st.columns([1, 1])

    with corr_col:
        _render_correlation_panel(database_path)
        st.button(
            "Open incident correlation →",
            key="cc-open-correlation",
            on_click=_navigate_dashboard_tab,
            args=("Intelligence",),
        )

    with response_col:
        _render_response_coordination_panel(database_path)
        st.button(
            "Manage response teams →",
            key="cc-open-response",
            on_click=_navigate_dashboard_tab,
            args=("Response",),
        )

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Geographical Intelligence + Charts ─────────────────────
    geo_col, charts_col = st.columns([1.2, 1])

    with geo_col:
        _render_geographical_panel(incidents, database_path)

    with charts_col:
        if incidents:
            # Priority Distribution chart
            st.markdown(
                """
                <div class="cc-panel-header">
                  <div class="cc-panel-indicator indigo"></div>
                  <div>
                    <div class="cc-panel-title">Operational Analytics</div>
                    <div class="cc-panel-subtitle">Priority & Status Distribution</div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            priorities = (
                pd.Series([item["severity"] for item in incidents])
                .value_counts()
                .rename_axis("Priority")
                .to_frame("Incidents")
            )
            st.bar_chart(priorities, color="#22D3EE")

            statuses = (
                pd.Series([item["status"] for item in incidents])
                .value_counts()
                .rename_axis("Status")
                .to_frame("Incidents")
            )
            st.bar_chart(statuses, color="#6366F1")
        else:
            st.info("Operational analytics will appear when incident records exist.")

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

    # ── Recent Reports table ───────────────────────────────────
    _render_recent_reports_panel(database_path, incidents)


def _render_situation_report(database_path: Path, actor: str) -> None:
    st.subheader("Situation reports")
    st.caption(
        "This is an AI-generated draft based only on submitted records. "
        "Edit the draft and mark it reviewed to save an auditable operator review."
    )
    if st.button("Generate AI situation report draft", type="primary"):
        incidents = fetch_incidents(database_path)
        try:
            with st.spinner("Drafting a situation report..."):
                draft = generate_situation_report(GeminiClient(), incidents)
            report_id = create_situation_report_draft(
                database_path,
                draft,
                actor=actor,
            )
        except (GeminiRequestError, ValueError, sqlite3.Error, OSError) as error:
            st.error(f"Could not generate the draft: {error}")
        else:
            st.session_state["selected_situation_report_id"] = report_id
            st.rerun()

    reports = fetch_situation_reports(database_path)
    if not reports:
        st.info("No situation reports have been generated.")
        return

    labels = {
        f"#{report['id']} · {report['review_status']} · {report['generated_at']}":
        report["id"]
        for report in reports
    }
    default_report_id = st.session_state.get(
        "selected_situation_report_id",
        reports[0]["id"],
    )
    default_index = next(
        (
            index
            for index, report in enumerate(reports)
            if report["id"] == default_report_id
        ),
        0,
    )
    selected_label = st.selectbox(
        "Draft history",
        list(labels),
        index=default_index,
        key="situation-report-selection",
    )
    selected_report_id = labels[selected_label]
    st.session_state["selected_situation_report_id"] = selected_report_id
    report = next(item for item in reports if item["id"] == selected_report_id)
    editor_key = f"situation-report-editor-{selected_report_id}"
    content = st.text_area(
        "Review and edit report",
        value=report["content"],
        height=280,
        key=editor_key,
        disabled=report["review_status"] == "Reviewed",
    )
    if report["review_status"] == "Draft":
        if st.button("Save and mark as reviewed", type="primary"):
            try:
                review_situation_report(
                    database_path,
                    selected_report_id,
                    content,
                    reviewer=actor,
                )
            except (ValueError, sqlite3.Error) as error:
                st.error(f"Could not save the reviewed report: {error}")
            else:
                st.success("Reviewed report saved and logged.")
                st.rerun()
    else:
        st.success(
            f"Reviewed by {report['reviewed_by']} at {report['reviewed_at']}."
        )


def _render_audit_log(database_path: Path) -> None:
    st.subheader("Operator activity log")
    entries = fetch_activity_log(database_path)
    if not entries:
        st.info("No activity has been recorded yet.")
        return
    st.dataframe(
        [
            {
                "Time": entry["created_at"],
                "Actor": entry["actor"],
                "Action": entry["action"],
                "Report ID": entry["report_id"],
                "Incident ID": entry["incident_id"],
                "Details": entry["details_json"],
            }
            for entry in entries
        ],
        width="stretch",
        hide_index=True,
    )


def render_dashboard() -> None:
    actor = st.session_state.get("operator_name", "Operator")
    incidents = fetch_incidents(settings.database_path)
    needs_review_count = sum(
        incident["status"] in {"New", "Under Review"}
        for incident in incidents
    )
    _render_top_navigation(needs_review_count)

    # Title area
    st.markdown(
        '<div class="cc-title-area">'
        '<div class="cc-title">Command Center</div>'
        '<div class="cc-tagline">'
        '<em>See the Threat. Understand the Impact. Coordinate the Response.</em>'
        ' — Peshawar, Khyber Pakhtunkhwa'
        '</div></div>',
        unsafe_allow_html=True,
    )

    navigation_items = [
        "Command Center",
        "Intelligence",
        "Incidents",
        "Live Map",
        "Response",
        "Digital Twin",
        "Dispatch Queue",
        "Situation Reports",
        "Audit Log",
    ]

    with st.sidebar:
        st.markdown(
            '<div class="aegis-brand-name">AEGIS <span>X</span></div>'
            '<div class="aegis-brand-subtitle">Operations workspace</div>',
            unsafe_allow_html=True,
        )
        st.markdown("---")
        st.radio(
            "Workspace navigation",
            navigation_items,
            key="dashboard-navigation",
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.caption("Decision support only · Operator-led review")

    selected_page = st.session_state["dashboard-navigation"]
    if selected_page == "Command Center":
        _render_command_center(settings.database_path, incidents)
    elif selected_page == "Intelligence":
        render_report_center(settings.database_path)
    elif selected_page == "Incidents":
        render_incident_manager(settings.database_path, actor)
    elif selected_page == "Live Map":
        render_live_map(incidents)
    elif selected_page == "Response":
        render_resource_coordination(
            settings.database_path,
            incidents,
            actor,
        )
    elif selected_page == "Digital Twin":
        render_digital_twin(
            settings.database_path,
            incidents,
            fetch_response_teams(settings.database_path),
            fetch_resource_assignments(settings.database_path),
            actor,
        )
    elif selected_page == "Dispatch Queue":
        render_dispatch_queue(settings.database_path, incidents, actor)
    elif selected_page == "Situation Reports":
        _render_situation_report(settings.database_path, actor)
    elif selected_page == "Audit Log":
        _render_audit_log(settings.database_path)
