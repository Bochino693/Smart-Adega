from django.contrib import admin
from .models import (
    CategoriaProduto, Produtos, Estoque, Venda, ItemVenda,
    SaidaEstoque, CategoriaDespesas, Despesa, FinanceiroMes
)

# ======================
# CATEGORIAS DE PRODUTOS
# ======================
@admin.register(CategoriaProduto)
class CategoriasProdutosAdmin(admin.ModelAdmin):
    list_display = ('nome_categoria', 'ativo', 'criacao', 'atualizado')
    search_fields = ('nome_categoria',)
    list_filter = ('ativo',)
    ordering = ('nome_categoria',)


# ============
# PRODUTOS
# ============
@admin.register(Produtos)
class ProdutosAdmin(admin.ModelAdmin):
    list_display = ('nome_produto', 'categoria', 'preco_venda', 'preco_fornecedor', 'ganho_potencial', 'ativo')
    search_fields = ('nome_produto', 'codigo')
    list_filter = ('categoria', 'ativo')
    ordering = ('nome_produto',)
    autocomplete_fields = ('categoria',)
    readonly_fields = ('ganho_potencial',)


# ============
# ESTOQUE
# ============
@admin.register(Estoque)
class EstoqueAdmin(admin.ModelAdmin):
    list_display = ('produtos', 'data_validade', 'lote', 'quantidade', 'ativo')
    list_filter = ('produtos', 'data_validade', 'ativo')
    search_fields = ('produtos__nome_produto',)
    ordering = ('produtos__nome_produto', 'data_validade')


# ===================
# SAÍDA DE ESTOQUE
# ===================
@admin.register(SaidaEstoque)
class SaidaEstoqueAdmin(admin.ModelAdmin):
    list_display = ('nome_produto', 'codigo_produto', 'quantidade', 'data_saida', 'ativo')
    search_fields = ('nome_produto', 'codigo_produto')
    list_filter = ('data_saida',)
    ordering = ('-data_saida',)
    readonly_fields = ('nome_produto', 'codigo_produto', 'data_saida')


# ===============
# DINHEIRO CAIXA
# ===============


# ===========
# VENDAS
# ===========
class ItemVendaInline(admin.TabularInline):
    model = ItemVenda
    extra = 1
    fields = ('produto', 'quantidade', 'valor_unitario', 'desconto', 'valor_total')
    readonly_fields = ('valor_total',)


@admin.register(Venda)
class VendaAdmin(admin.ModelAdmin):
    list_display = ('id', 'usuario', 'forma_pagamento', 'valor_liquido', 'desconto_total', 'data')
    list_filter = ('forma_pagamento', 'data')
    search_fields = ('usuario__username',)
    date_hierarchy = 'data'
    inlines = [ItemVendaInline]


@admin.register(ItemVenda)
class ItemVendaAdmin(admin.ModelAdmin):
    list_display = ('id', 'venda', 'produto', 'quantidade', 'valor_unitario', 'desconto', 'valor_total')
    list_filter = ('produto',)
    search_fields = ('produto__nome_produto', 'venda__id')


# ===================
# CATEGORIAS DE DESPESAS
# ===================
@admin.register(CategoriaDespesas)
class CategoriaDespesasAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo', 'criacao', 'atualizado')
    search_fields = ('nome',)
    ordering = ('nome',)


# ===========
# DESPESAS
# ===========
@admin.register(Despesa)
class DespesaAdmin(admin.ModelAdmin):
    list_display = ('descricao', 'categoria', 'criacao', 'valor', 'ativo')
    list_filter = ('categoria', 'criacao', 'ativo')
    search_fields = ('descricao',)
    ordering = ('-criacao',)


# ===================
# FINANCEIRO MENSAL
# ===================
from django.contrib import admin
from .models import FinanceiroMes

@admin.register(FinanceiroMes)
class FinanceiroMesAdmin(admin.ModelAdmin):
    list_display = (
        "mes_nome",
        "ano",
        "total_liquido",
        "total_ganho_potencial",
        "total_despesas",
        "lucro_liquido",
        "lucro_potencial",
        "criacao",

    )
    list_filter = ("ano", "mes")
    ordering = ("-ano", "-mes")
    search_fields = ("ano",)
    readonly_fields = (
        "total_liquido",
        "total_ganho_potencial",
        "total_despesas",
        "lucro_liquido",
        "lucro_potencial",
        "criacao"

    )

    actions = ["gerar_ou_atualizar_selecionados"]

    fieldsets = (
        ("Período", {"fields": ("mes", "ano")}),
        ("Totais Calculados", {
            "fields": (
                "total_liquido",
                "total_ganho_potencial",
                "total_despesas",
                "lucro_liquido",
                "lucro_potencial",
                "criacao",
            )
        }),
        ("Metadados", {"fields": ("criado_em",)}),
    )

    def mes_nome(self, obj):
        meses = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ]
        return meses[obj.mes - 1]
    mes_nome.short_description = "Mês"

    @admin.action(description="🔄 Gerar ou atualizar fechamento selecionado(s)")
    def gerar_ou_atualizar_selecionados(self, request, queryset):
        count = 0
        for f in queryset:
            FinanceiroMes.gerar_ou_atualizar(f.mes, f.ano)
            count += 1
        self.message_user(request, f"{count} fechamento(s) atualizado(s) com sucesso ✅")

