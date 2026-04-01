"""
Fix the classification_report.json by correcting misclassified firm assignments.

This script:
1. Reads the classification report
2. Corrects assigned_firm_name for each artifact based on:
   - _recovery.original_firm_name (when available and correct)
   - Manual fund→firm mapping (for empty originals)
3. Merges duplicate firm names to canonical forms
4. Writes the corrected report back

Run BEFORE deleting folders/mappings and re-running the pipeline.
"""

import json
import copy
from pathlib import Path

REPORT_PATH = Path("/data/Hedge Funds/classification_report.json")

# ============================================================
# FIRM NAME CORRECTIONS
# ============================================================

# Merge duplicate firm names → single canonical
FIRM_MERGES = {
    "HAO CAPITAL MANAGEMENT": "HAO ADVISORS MANAGEMENT",
    "HAMILTON SQUARE PARTNERS MANAGEMENT": "HAMILTON SQUARE HEALTHCARE PARTNERS",
    "THE BAINBRIDGE COMPANIES": "BAINBRIDGE PARTNERS",
    "LONG CORRIDOR ASSET MANAGEMENT LIMITED (LCAM)": "LONG CORRIDOR ASSET MANAGEMENT",
    "LONG CORRIDOR ASSET MANAGEMENT (LONG CORRIDOR ASSET MANAGEMENT LIMITED)": "LONG CORRIDOR ASSET MANAGEMENT",
    "METRICA PARTNERS PTE.": "METRICA PARTNERS",
    "METRICA PARTNERS, SINGAPORE": "METRICA PARTNERS",
    "MONACO ASSET MANAGEMENT S.A.M": "MONACO ASSET MANAGEMENT",
    "MONACO ASSET MANAGEMENT S.A.M.": "MONACO ASSET MANAGEMENT",
    "GVA ASSET MANAGEMENT CO.": "GVA ASSET MANAGEMENT",
    "EDGESTREAM PARTNERS, L.P.": "EDGESTREAM PARTNERS",
    "THIRD_PARTY MORGAN STANLEY": "THIRD_PARTY MORGAN STANLEY",  # keep as-is
    "TISCO ASSET MANAGEMENT": "E20 CAPITAL",  # TISCO only contains E20, misassigned
    "ZETA ASSET MANAGEMENT ICC": "FAIRLIGHT CAPITAL",  # ZETA only contains Bowmoor
    "BOWMOOR GLOBAL ALPHA": "FAIRLIGHT CAPITAL",  # Bowmoor is managed by Fairlight
}

# For WT artifacts where original_firm_name is available, map GPT originals → canonical
ORIGINAL_FIRM_TO_CANONICAL = {
    "Springs Capital": "SPRINGS CAPITAL",
    "Springs Capital (Hong Kong) limited": "SPRINGS CAPITAL",
    "Springs Capital (Hong Kong) Limited": "SPRINGS CAPITAL",
    "SPRINGS CAPITAL": "SPRINGS CAPITAL",
    "AQUIS Capital AG": "AQUIS CAPITAL",
    "AQUIS Capital": "AQUIS CAPITAL",
    "Aquis Capital": "AQUIS CAPITAL",
    "SG Capital": "SG CAPITAL",
    "TabCap Investment Management": "TABCAP INVESTMENT MANAGEMENT",
    "S. W. Mitchell Capital LLP": "SW MITCHELL CAPITAL",
    "Gembridge Capital Management": "GEMBRIDGE CAPITAL",
    "GEMBRIDGE CAPITAL MANAGEMENT": "GEMBRIDGE CAPITAL",
    "Gembridge Capital, Singapore": "GEMBRIDGE CAPITAL",
    "Medit Capital": "MEDIT CAPITAL",
    "Medit Capital Partners LP": "MEDIT CAPITAL",
    "Orgueil Capital": "ORGUEIL CAPITAL",
    "ORGUEIL CAPITAL": "ORGUEIL CAPITAL",
    "Liquibit Capital": "LIQUIBIT CAPITAL",
    "Liquibit": "LIQUIBIT CAPITAL",
    "LIQUIBIT CAPITAL": "LIQUIBIT CAPITAL",
    "AG Capital": "AG CAPITAL",
    "Graham Capital Management": "GRAHAM CAPITAL MANAGEMENT",
    "Blair Road Capital": "BLAIR ROAD CAPITAL",
    "BLAIR ROAD CAPITAL": "BLAIR ROAD CAPITAL",
    "Fairlight Capital Partners": "FAIRLIGHT CAPITAL",
    "FAIRLIGHT CAPITAL": "FAIRLIGHT CAPITAL",
    "Fairlight Capital": "FAIRLIGHT CAPITAL",
    "ARNOTT CAPITAL": "ARNOTT CAPITAL",
    "Bellecapital International AG": "BELLECAPITAL",
    "E20 Capital Limited": "E20 CAPITAL",
    "FOREST AVENUE CAPITAL": "FOREST AVENUE CAPITAL",
    "Forest Avenue Capital Management LP": "FOREST AVENUE CAPITAL",
    "Statar Capital": "STATAR CAPITAL",
    "Institutional Capital Partners, LLC": "INSTITUTIONAL CAPITAL PARTNERS",
    "Shiprock Capital Management Limited": "SHIPROCK CAPITAL",
    "Golden Nest Capital Management": "GOLDEN NEST CAPITAL",
    "Golden Nest Capital": "GOLDEN NEST CAPITAL",
    "GOLDEN NEST CAPITAL": "GOLDEN NEST CAPITAL",
    "Golden Nest Capital Management (Hong Kong)": "GOLDEN NEST CAPITAL",
    "System 2 Capital": "SYSTEM 2 CAPITAL",
    "MELANION CAPITAL": "MELANION CAPITAL",
    "Melanion": "MELANION CAPITAL",
    "KATE CAPITAL LLC": "KATE CAPITAL",
    "ARP Global Capital Limited": "ARP GLOBAL CAPITAL",
    "Aregence Capital Management": "AREGENCE CAPITAL",
    "WaterValley Capital Management (HK) Ltd.": "WATERVALLEY CAPITAL",
    "Muirfield Capital Global Advisors": "MUIRFIELD CAPITAL",
    "Kultura Capital Management LP": "KULTURA CAPITAL",
    "BNP Paribas": "BNP PARIBAS",
    "AVM Capital": "AVM CAPITAL",
    "Advent Capital Management, LLC": "ADVENT CAPITAL MANAGEMENT",
    "AQR Capital Management, LLC": "AQR CAPITAL MANAGEMENT",
    "Axonic Capital": "AXONIC CAPITAL",
    "Castiglione Capital": "CASTIGLIONE CAPITAL",
    "Crabel Capital Management": "CRABEL CAPITAL MANAGEMENT",
    "Discovery Capital Management, LLC": "DISCOVERY CAPITAL MANAGEMENT",
    "Episteme Capital": "EPISTEME CAPITAL",
    "John Street Capital Limited": "JOHN STREET CAPITAL",
    "Kepos Capital LP": "KEPOS CAPITAL",
    "Massar Capital Management": "MASSAR CAPITAL MANAGEMENT",
    "Troluce Capital Advisors, LLC": "TROLUCE CAPITAL ADVISORS",
    "Vinland Capital Management": "VINLAND CAPITAL MANAGEMENT",
    "Aspen Capital, Hong Kong": "ASPEN GLOBAL MANAGEMENT",
    "Candelo Capital": "CANDELO CAPITAL",
    "Quantica Capital": "QUANTICA CAPITAL",
    "Camphora Capital Inc.": "CAMPHORA CAPITAL",
    "Harvey Capital Partners": "HARVEY CAPITAL PARTNERS",
    "Marek Capital": "MAREK CAPITAL",
    "Metalith Capital Group": "METALITH CAPITAL",
    "Routeburn Capital": "ROUTEBURN CAPITAL",
    "Sarda Capital": "SARDA CAPITAL",
    "CIBRA Capital Ltd": "CIBRA CAPITAL",
    "Ninth Avenue Capital": "NINTH AVENUE CAPITAL",
    "24A Capital Management": "24A CAPITAL MANAGEMENT",
    "Arène Capital Partners": "ARENE CAPITAL PARTNERS",
    "Jerpoint Capital (UK) Management Ltd": "JERPOINT CAPITAL",
    "Setara Capital Management": "SETARA CAPITAL",
    "LeFevre Park Capital": "LEFEVRE PARK CAPITAL",
    "L1 Capital": "L1 CAPITAL",
    "WT ASSET MANAGEMENT": "WT ASSET MANAGEMENT",  # actual WT
    "GOLDMAN SACHS": "GOLDMAN SACHS",
    "Bowmoor Global Alpha": "FAIRLIGHT CAPITAL",
    "BOWMOOR GLOBAL ALPHA": "FAIRLIGHT CAPITAL",
}

# For WT artifacts with EMPTY original_firm_name, map fund name → correct firm
# These were assigned via poisoned mappings_lookup
FUND_TO_FIRM = {
    # WT's actual funds
    "WT CHINA FUND LIMITED": "WT ASSET MANAGEMENT",
    "WT CHINA OFFSHORE FUND LIMITED": "WT ASSET MANAGEMENT",
    "WT China Focus Fund": "WT ASSET MANAGEMENT",
    "WT China Fund": "WT ASSET MANAGEMENT",
    "Greater China Equity Long Short Fund": "WT ASSET MANAGEMENT",
    # Melanion / Sigma
    "Melanion Alpha Strategy": "MELANION CAPITAL",
    "Melanion ARB Strategy": "MELANION CAPITAL",
    "Melanion Arb Strategy": "MELANION CAPITAL",
    "Melanion Volatility Strategy": "MELANION CAPITAL",
    "Sigma Market Neutral Fund": "MELANION CAPITAL",
    "Alpha Fund": "MELANION CAPITAL",
    "Arb Fund": "MELANION CAPITAL",
    "Volatility Fund": "MELANION CAPITAL",
    # Japan Catalyst
    "Japan Catalyst Fund": "JAPAN CATALYST",
    "JapanCatalystFund": "JAPAN CATALYST",
    # Bowmoor / Fairlight
    "Bowmoor Global Alpha": "FAIRLIGHT CAPITAL",
    "Bowmoor Global Alpha Fund": "FAIRLIGHT CAPITAL",
    "Bowmoor Global Alpha fund": "FAIRLIGHT CAPITAL",
    # Golden Nest
    "Golden Nest Greater China Fund": "GOLDEN NEST CAPITAL",
    "鑫巢大中华基金 (Golden Nest Greater China Fund)": "GOLDEN NEST CAPITAL",
    # Medit
    "Medit Capital": "MEDIT CAPITAL",
    "Medit Capital Offshore Fund, Ltd": "MEDIT CAPITAL",
    # AG Capital
    "AG Capital": "AG CAPITAL",
    "AG Capital Onshore Fund, LP": "AG CAPITAL",
    "AG Capital Onshore Fund, LP 1": "AG CAPITAL",
    # E20
    "E20": "E20 CAPITAL",
    "E20 GLOBAL OPPORTUNITY INVESTMENT FUND LIMITED": "E20 CAPITAL",
    # Kultura
    "Kultura Capital": "KULTURA CAPITAL",
    "Kultura Capital Management LP": "KULTURA CAPITAL",
    # L1 Capital
    "L1 Capital Global Long Short (Offshore Feeder) Fund": "L1 CAPITAL",
    "L1 Capital Global Long Short Fund": "L1 CAPITAL",
    "L1 Capital Long Short": "L1 CAPITAL",
    # Liquibit
    "Liquibit": "LIQUIBIT CAPITAL",
    "Liquibit Flagship Fund": "LIQUIBIT CAPITAL",
    "Liquibit Market Neutral Arbitrage Fund": "LIQUIBIT CAPITAL",
    "Liquibit Market Neutral Arbitrage Fund / Liquibit Flagship Fund": "LIQUIBIT CAPITAL",
    # System 2
    "System 2": "SYSTEM 2 CAPITAL",
    "System 2 Capital Master Fund Limited": "SYSTEM 2 CAPITAL",
    # Shiprock
    "Shiprock": "SHIPROCK CAPITAL",
    "Shiprock Capital Master Fund": "SHIPROCK CAPITAL",
    # Springs
    "Springs Capital LTDA": "SPRINGS CAPITAL",
    "Springs China Opportunities Fund": "SPRINGS CAPITAL",
    # QIO / NavBackoffice
    "QIO": "QIO FUND MANAGEMENT",
    "QIO Fund": "QIO FUND MANAGEMENT",
    "Value Opportunities VCC (QIO)": "QIO FUND MANAGEMENT",
    # TabCap
    "TabCap Balanced Credit Fund": "TABCAP INVESTMENT MANAGEMENT",
    "TabCap Balanced Credit Fund (TBAL)": "TABCAP INVESTMENT MANAGEMENT",
    "Tabula Balanced Credit Fund": "TABCAP INVESTMENT MANAGEMENT",
    # Ardtur / SW Mitchell
    "Ardtur European Focus Absolute Return Fund": "SW MITCHELL CAPITAL",
    "Ardtur European Focus Absolute Return Fund (EUR I)": "SW MITCHELL CAPITAL",
    "Ardtur European L/O & L/S Equity Strategy": "SW MITCHELL CAPITAL",
    # AVM
    "AVM Global Opportunity Fund": "AVM CAPITAL",
    # Arnott
    "Arnott Opportunities Fund": "ARNOTT CAPITAL",
    # Forest Avenue
    "Forest Avenue Capital (the Fund)": "FOREST AVENUE CAPITAL",
    # AQUIS
    "AQUIS Lumen Vietnam Fund UCITS": "AQUIS CAPITAL",
    "Lumen Vietnam Fund": "AQUIS CAPITAL",
    # Teton / SG Capital
    "Teton Concentrated": "SG CAPITAL",
    "Teton Concentrated Fund": "SG CAPITAL",
    "Teton Concentrated Long Short": "SG CAPITAL",
    "Teton Concentrated Long Short Class D": "SG CAPITAL",
    "SG Capital's Teton Concentrated Fund": "SG CAPITAL",
    "SG Capital\u2019s Teton Concentrated Fund": "SG CAPITAL",
    # Multi-Alpha / New Holland (from GS Cap Intro — these are real funds)
    "Multi-Alpha Opportunity": "GOLDMAN SACHS",
    "New Holland Capital - Tactical Alpha Fund (TAF)": "GOLDMAN SACHS",
    "TAF": "GOLDMAN SACHS",
    # BelleCapital
    "BelleCapital": "BELLECAPITAL",
    "BelleCapital Core Europe Eiger Equity Strategy": "BELLECAPITAL",
    "Belle Core Europe Eiger Equity Strategy": "BELLECAPITAL",
    "Belle Core Europe Eiger Fund": "BELLECAPITAL",
    "Eiger Fund": "BELLECAPITAL",
    # Divisadero Street (from GS Cap Intro)
    "Divisadero Street": "GOLDMAN SACHS",
    "Divisadero Street Fund": "GOLDMAN SACHS",
    # Svelland
    "Svelland Global Trading Fund": "GOLDMAN SACHS",
    # Quantica
    "Quantica Managed Futures (QMF) Program": "QUANTICA CAPITAL",
    # Candelo (from Marex cap intro)
    "Candelo": "CANDELO CAPITAL",
    # GCM Global
    "GCM Global Fixed Income Alpha Fund": "GOLDMAN SACHS",
    # Isabella (from BNPP cap intro)
    "Isabella Investments Master Fund": "GOLDMAN SACHS",
    # the Fund (generic, can't determine)
    "the Fund": "WT ASSET MANAGEMENT",  # keep under WT as unknown
    # LFP
    "LFP": "GOLDMAN SACHS",
    # Pi Radians
    "Pi Radians Fund": "GOLDMAN SACHS",
    # Polymer Asia
    "Polymer Asia Fund": "GOLDMAN SACHS",
    # Cedar Street
    "Cedar Street Market Neutral Fund": "GOLDMAN SACHS",
    # GS conference manager lists (these are individual fund managers from GS events)
    "33 Capital Management LLC": "GOLDMAN SACHS",
    "Arene Capital Partners": "ARENE CAPITAL PARTNERS",
    "B ravix Capital": "GOLDMAN SACHS",
    "Baiont Quant Attention Research Expedition Fund I": "GOLDMAN SACHS",
    "Black Ark Capital Ltd": "GOLDMAN SACHS",
    "Bravix Capital": "GOLDMAN SACHS",
    "Covara Capital, LLC": "GOLDMAN SACHS",
    "Curisa Capital": "GOLDMAN SACHS",
    "Fremen Capital Management LP": "GOLDMAN SACHS",
    "Galaxy Asset Management": "GOLDMAN SACHS",
    "Graphene Capital Management LP": "GOLDMAN SACHS",
    "Haverstock Capital": "GOLDMAN SACHS",
    "Kalonek Capital": "GOLDMAN SACHS",
    "Marek Capital": "GOLDMAN SACHS",
    "Noventa Capital Management": "GOLDMAN SACHS",
    "Pelham Capital Ltd - Bayes Hill": "GOLDMAN SACHS",
    "Rock2 Capital": "GOLDMAN SACHS",
    "Routeburn Capital": "GOLDMAN SACHS",
    "SMA Capital LLC": "GOLDMAN SACHS",
    "Saber Capital": "GOLDMAN SACHS",
    "Sarda Capital": "GOLDMAN SACHS",
    "Setara Capital Management": "GOLDMAN SACHS",
    "Vinland Capital Management": "GOLDMAN SACHS",
}


def fix_report():
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    report = copy.deepcopy(report)
    stats = {"fixed_from_original": 0, "fixed_from_fund_map": 0, "firm_merged": 0, "unchanged": 0, "unresolved": 0}

    for entry in report.get("classifications", []):
        arts = entry.get("artifact_assignments", {})
        for item in arts.get("included_attachments", []) + arts.get("included_links", []):
            firm = item.get("assigned_firm_name", "")
            fund = item.get("assigned_fund_name", "")
            rec = item.get("_recovery", {})
            orig_firm = rec.get("original_firm_name", "")

            new_firm = None

            # Case 1: WT artifact — needs reclassification
            if firm == "WT ASSET MANAGEMENT":
                # Try original_firm_name first
                if orig_firm and orig_firm in ORIGINAL_FIRM_TO_CANONICAL:
                    new_firm = ORIGINAL_FIRM_TO_CANONICAL[orig_firm]
                    stats["fixed_from_original"] += 1
                elif orig_firm and orig_firm != "WT ASSET MANAGEMENT":
                    # Original exists but not in our map — use it uppercased
                    new_firm = orig_firm.upper().strip()
                    stats["fixed_from_original"] += 1
                elif fund and fund in FUND_TO_FIRM:
                    new_firm = FUND_TO_FIRM[fund]
                    stats["fixed_from_fund_map"] += 1
                elif not fund and not orig_firm:
                    # Empty fund and empty original — leave as WT
                    new_firm = "WT ASSET MANAGEMENT"
                    stats["unchanged"] += 1
                else:
                    # Unresolved — print for review
                    print(f"UNRESOLVED: firm={firm} fund={fund} orig={orig_firm}")
                    stats["unresolved"] += 1
                    continue

            # Case 2: Firm merge (duplicate firm names)
            elif firm in FIRM_MERGES:
                new_firm = FIRM_MERGES[firm]
                stats["firm_merged"] += 1
            else:
                stats["unchanged"] += 1
                continue

            if new_firm and new_firm != firm:
                item["assigned_firm_name"] = new_firm

        # Also fix canonical_firm_name at entry level
        canonical = entry.get("canonical_firm_name", "")
        if canonical == "WT ASSET MANAGEMENT":
            # Re-derive from first included artifact
            all_included = arts.get("included_attachments", []) + arts.get("included_links", [])
            for item in all_included:
                if item.get("assigned_firm_name"):
                    entry["canonical_firm_name"] = item["assigned_firm_name"]
                    break
        elif canonical in FIRM_MERGES:
            entry["canonical_firm_name"] = FIRM_MERGES[canonical]

    print(f"\n=== STATS ===")
    print(f"  Fixed from original_firm_name: {stats['fixed_from_original']}")
    print(f"  Fixed from fund→firm map:      {stats['fixed_from_fund_map']}")
    print(f"  Firm merges applied:           {stats['firm_merged']}")
    print(f"  Unchanged:                     {stats['unchanged']}")
    print(f"  Unresolved:                    {stats['unresolved']}")

    # Write corrected report
    output_path = REPORT_PATH.parent / "classification_report_fixed.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nWrote corrected report to: {output_path}")

    return report


if __name__ == "__main__":
    fix_report()
