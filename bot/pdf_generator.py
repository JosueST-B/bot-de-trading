import os
import logging
from datetime import datetime
from fpdf import FPDF

# Configurar logging básico por si no está configurado
logging.basicConfig(level=logging.INFO)

class PDFReportGenerator:
    @staticmethod
    def generate_investor_report(
        user_name: str,
        email: str,
        trades: list,
        initial_balance: float,
        performance_fee_pct: float = 0.20
    ) -> str:
        """Genera un reporte PDF formal e institucional para el inversor."""
        # 1. Calcular Métricas
        total_trades = len(trades)
        wins = [t for t in trades if t.get("pnl", 0.0) > 0.0]
        
        win_rate = (len(wins) / total_trades * 100.0) if total_trades > 0 else 0.0
        total_pnl = sum(t.get("pnl", 0.0) for t in trades)
        final_balance = initial_balance + total_pnl
        roi_pct = (total_pnl / initial_balance * 100.0) if initial_balance > 0 else 0.0
        
        # Comisión sobre ganancias (solo si el P&L neto es positivo)
        performance_fee = max(0.0, total_pnl * performance_fee_pct)
        
        # 2. Inicializar FPDF
        pdf = FPDF()
        pdf.add_page()
        pdf.set_auto_page_break(auto=True, margin=15)
        
        # Paleta de Colores
        c_navy = (20, 40, 80)
        c_gray = (240, 240, 240)
        c_dark = (50, 50, 50)
        
        # Encabezado (Logo Textual del Holding)
        pdf.set_font("Helvetica", "B", 18)
        pdf.set_text_color(*c_navy)
        pdf.cell(0, 10, "BERKSHIRE HATHAWAY QUANT HOLDING", new_x="LMARGIN", new_y="NEXT", align="L")
        
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*c_dark)
        pdf.cell(0, 5, "Reporte Mensual de Desempeño y Liquidación de Cuentas", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.cell(0, 5, f"Fecha de emisión: {datetime.now().strftime('%Y-%m-%d %H:%M')}", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(5)
        
        # Línea divisoria azul
        pdf.set_draw_color(*c_navy)
        pdf.set_line_width(0.8)
        pdf.line(10, 32, 200, 32)
        pdf.ln(8)
        
        # Sección 1: Información del Inversor
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*c_navy)
        pdf.cell(0, 8, "1. Información de la Cuenta del Inversor", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(2)
        
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*c_dark)
        
        # Caja Gris de Datos
        pdf.set_fill_color(*c_gray)
        pdf.cell(190, 25, "", border=1, fill=True)
        pdf.set_xy(12, 45)
        pdf.cell(90, 5, f"Inversor: {user_name}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(12)
        pdf.cell(90, 5, f"Email registrado: {email}", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(12)
        pdf.cell(90, 5, f"Tipo de Cuenta: PAMM Automatizada TWS/Binance", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_xy(110, 45)
        pdf.cell(90, 5, f"Fórmula de Desempeño: {performance_fee_pct:.0%} High-Water Mark", new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(110)
        pdf.cell(90, 5, f"Estado de Cuenta: AUDITADA / ACTIVA", new_x="LMARGIN", new_y="NEXT")
        
        pdf.set_xy(10, 75)
        
        # Sección 2: Métricas Financieras y Liquidación de Comisión
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*c_navy)
        pdf.cell(0, 8, "2. Resumen Financiero y Liquidación de Rendimientos", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(2)
        
        # Tabla de Indicadores Financieros
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(*c_navy)
        pdf.set_text_color(255, 255, 255)
        
        pdf.cell(38, 8, "Balance Inicial", border=1, align="C", fill=True)
        pdf.cell(38, 8, "Balance Final", border=1, align="C", fill=True)
        pdf.cell(38, 8, "P&L Bruto", border=1, align="C", fill=True)
        pdf.cell(38, 8, "Comisión (20%)", border=1, align="C", fill=True)
        pdf.cell(38, 8, "ROI Neto", border=1, align="C", fill=True)
        pdf.ln(8)
        
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*c_dark)
        
        pdf.cell(38, 8, f"${initial_balance:,.2f}", border=1, align="C")
        pdf.cell(38, 8, f"${final_balance:,.2f}", border=1, align="C")
        
        # Color del P&L bruto
        pnl_color = (0, 128, 0) if total_pnl >= 0 else (180, 0, 0)
        pdf.set_text_color(*pnl_color)
        pdf.cell(38, 8, f"${total_pnl:+,.2f}", border=1, align="C")
        
        pdf.set_text_color(*c_dark)
        pdf.cell(38, 8, f"${performance_fee:,.2f}", border=1, align="C")
        
        pdf.set_text_color(*pnl_color)
        pdf.cell(38, 8, f"{roi_pct:+.2f}%", border=1, align="C")
        pdf.ln(12)
        
        # Métricas de Operaciones
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(*c_navy)
        pdf.cell(60, 5, f"Operaciones Totales: {total_trades}")
        pdf.cell(60, 5, f"Ratio de Éxito (Win Rate): {win_rate:.1f}%", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)
        
        # Sección 3: Detalle de Transacciones
        pdf.set_font("Helvetica", "B", 12)
        pdf.set_text_color(*c_navy)
        pdf.cell(0, 8, "3. Bitácora de Transacciones Cerradas", new_x="LMARGIN", new_y="NEXT", align="L")
        pdf.ln(2)
        
        # Encabezado de la tabla de transacciones
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(220, 225, 235)
        pdf.set_text_color(*c_navy)
        
        pdf.cell(35, 6, "Fecha Entrada", border=1, align="C", fill=True)
        pdf.cell(35, 6, "Fecha Salida", border=1, align="C", fill=True)
        pdf.cell(25, 6, "Activo", border=1, align="C", fill=True)
        pdf.cell(20, 6, "Cantidad", border=1, align="C", fill=True)
        pdf.cell(25, 6, "P. Entrada", border=1, align="C", fill=True)
        pdf.cell(25, 6, "P. Salida", border=1, align="C", fill=True)
        pdf.cell(25, 6, "P&L ($)", border=1, align="C", fill=True)
        pdf.ln(6)
        
        pdf.set_font("Helvetica", "", 7.5)
        pdf.set_text_color(*c_dark)
        
        for idx, t in enumerate(trades[:15]): # Mostrar últimos 15 trades para que quepa en 1 página
            # Alternar color de fila
            fill = (idx % 2 == 0)
            if fill:
                pdf.set_fill_color(248, 248, 248)
            else:
                pdf.set_fill_color(255, 255, 255)
                
            pdf.cell(35, 5.5, str(t.get("entry_time", ""))[:19], border=1, align="C", fill=True)
            pdf.cell(35, 5.5, str(t.get("exit_time", ""))[:19], border=1, align="C", fill=True)
            pdf.cell(25, 5.5, str(t.get("symbol", "")), border=1, align="C", fill=True)
            pdf.cell(20, 5.5, f"{t.get('quantity', 0.0):.2f}", border=1, align="C", fill=True)
            pdf.cell(25, 5.5, f"${t.get('entry_price', 0.0):.2f}", border=1, align="C", fill=True)
            pdf.cell(25, 5.5, f"${t.get('exit_price', 0.0):.2f}", border=1, align="C", fill=True)
            
            pnl_val = t.get("pnl", 0.0)
            p_color = (0, 120, 0) if pnl_val >= 0 else (160, 0, 0)
            pdf.set_text_color(*p_color)
            pdf.cell(25, 5.5, f"${pnl_val:+.2f}", border=1, align="C", fill=True)
            pdf.set_text_color(*c_dark)
            pdf.ln(5.5)
            
        if len(trades) > 15:
            pdf.set_font("Helvetica", "I", 8)
            pdf.cell(0, 6, f"... y {len(trades) - 15} operaciones adicionales omitidas por espacio en el informe.", new_x="LMARGIN", new_y="NEXT", align="C")
            
        pdf.ln(12)
        
        # Firmas de Cierre
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*c_navy)
        pdf.set_xy(25, 245)
        pdf.cell(60, 4, "_________________________", align="C")
        pdf.set_xy(115, 245)
        pdf.cell(60, 4, "_________________________", new_x="LMARGIN", new_y="NEXT", align="C")
        
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*c_dark)
        pdf.set_xy(25, 250)
        pdf.cell(60, 4, "Administrador del Holding", align="C")
        pdf.set_xy(115, 250)
        pdf.cell(60, 4, f"Firma Inversor ({user_name})", new_x="LMARGIN", new_y="NEXT", align="C")
        
        # Guardar en archivo
        target_path = rf"C:\Users\USUARIO\reporte_inversor_{email}.pdf"
        pdf.output(target_path)
        logging.info(f"Reporte PDF de inversión guardado exitosamente en: {target_path}")
        return target_path
