"""
Tests de las funciones puras de backend/horas.py (fechas, validaciones):
sin dependencia de Odoo, Supabase ni Flask, así que no hace falta
mockear nada. El resto del módulo (_calcular_resumen,
_crear_linea_timesheet, etc.) sí depende de odoo_client y queda fuera
de este archivo a propósito.
"""

from datetime import date

import pytest

from backend.horas import (
    _fecha_valida,
    _validar_horas,
    dia_habil_anterior,
    dias_habiles_atras,
    parsear_fecha_busqueda,
)


class TestDiaHabilAnterior:
    def test_lunes_devuelve_el_viernes(self):
        lunes = date(2024, 1, 8)
        assert dia_habil_anterior(lunes) == date(2024, 1, 5)

    def test_domingo_devuelve_el_viernes(self):
        domingo = date(2024, 1, 7)
        assert dia_habil_anterior(domingo) == date(2024, 1, 5)

    def test_dia_de_semana_devuelve_el_dia_calendario_anterior(self):
        martes = date(2024, 1, 9)
        assert dia_habil_anterior(martes) == date(2024, 1, 8)

    def test_sabado_devuelve_el_viernes(self):
        sabado = date(2024, 1, 6)
        assert dia_habil_anterior(sabado) == date(2024, 1, 5)


class TestDiasHabilesAtras:
    def test_no_incluye_el_dia_desde(self):
        desde = date(2024, 1, 3)  # miércoles
        dias = dias_habiles_atras(5, desde=desde)
        assert desde not in dias

    def test_devuelve_la_cantidad_pedida_y_solo_dias_habiles(self):
        desde = date(2024, 1, 3)  # miércoles
        dias = dias_habiles_atras(5, desde=desde)
        assert len(dias) == 5
        assert all(d.weekday() < 5 for d in dias)

    def test_orden_del_mas_reciente_al_mas_antiguo(self):
        desde = date(2024, 1, 3)  # miércoles
        dias = dias_habiles_atras(5, desde=desde)
        assert dias == [
            date(2024, 1, 2),   # martes
            date(2024, 1, 1),   # lunes
            date(2023, 12, 29),  # viernes (salta el fin de semana)
            date(2023, 12, 28),  # jueves
            date(2023, 12, 27),  # miércoles
        ]


class TestParsearFechaBusqueda:
    def test_con_anio_de_4_digitos(self):
        assert parsear_fecha_busqueda("15/03/2024") == "2024-03-15"

    def test_con_anio_de_2_digitos(self):
        assert parsear_fecha_busqueda("05/01/24") == "2024-01-05"

    def test_sin_anio_usa_el_anio_actual(self):
        esperado = date(date.today().year, 3, 15).isoformat()
        assert parsear_fecha_busqueda("15/03") == esperado

    @pytest.mark.parametrize("texto", ["", "abc", "15", "32/13/2024", "15/13", "1/2/3/4"])
    def test_entradas_invalidas_devuelven_none(self, texto):
        assert parsear_fecha_busqueda(texto) is None


class TestValidarHoras:
    @pytest.mark.parametrize("valor,esperado", [("2", 2.0), ("2.5", 2.5), ("0.1", 0.1)])
    def test_numero_positivo_valido(self, valor, esperado):
        assert _validar_horas(valor) == esperado

    @pytest.mark.parametrize("valor", ["0", "-1", "abc", None, ""])
    def test_valores_invalidos_devuelven_none(self, valor):
        assert _validar_horas(valor) is None


class TestFechaValida:
    def test_fecha_iso_valida(self):
        assert _fecha_valida("2024-01-05") is True

    @pytest.mark.parametrize("valor", ["2024-13-01", "no-es-fecha", None, ""])
    def test_valores_invalidos(self, valor):
        assert _fecha_valida(valor) is False
