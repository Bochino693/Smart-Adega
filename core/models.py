from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.db.models import Sum, Count, Case, When, IntegerField, Q
from django.db import models
from django.contrib.auth.models import User


class Prime(models.Model):
    ativo = models.BooleanField(default=True)
    criacao = models.DateTimeField(auto_now_add=True, null=True)
    atualizado = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True


class CategoriaProduto(Prime):
    nome_categoria = models.CharField(max_length=90)

    class Meta:
        verbose_name = 'Categoria de Produto'
        verbose_name_plural = 'Categorias de Produtos'

    def clean(self):
        self.nome_categoria = self.nome_categoria.strip().title()
        if CategoriaProduto.objects.filter(nome_categoria__iexact=self.nome_categoria).exclude(pk=self.pk).exists():
            from django.core.exceptions import ValidationError
            raise ValidationError("Essa categoria já existe.")

    def __str__(self):
        return f'{self.nome_categoria}'


class Produtos(Prime):
    codigo = models.CharField(
        max_length=13,
        unique=True,
        null=True,
        blank=True,
        help_text="Código de barras ou identificador único do produto"
    )
    nome_produto = models.CharField(max_length=90, null=False, blank=False)
    preco_venda = models.DecimalField(decimal_places=2, max_digits=10, default=0)
    categoria = models.ForeignKey(CategoriaProduto, on_delete=models.CASCADE, blank=False, null=False)
    preco_fornecedor = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True)
    ganho_potencial = models.DecimalField(decimal_places=2, max_digits=10, null=True, blank=True, editable=False)
    # Novo campo de imagem
    imagem = models.ImageField(
        upload_to='produtos/',
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png'])],
        help_text="Imagem do produto (JPG ou PNG)"
    )

    class Meta:
        verbose_name = 'Produto'
        verbose_name_plural = 'Produtos'

    def save(self, *args, **kwargs):
        # Calcula ganho_potencial automaticamente
        if self.preco_venda is not None and self.preco_fornecedor is not None:
            self.ganho_potencial = self.preco_venda - self.preco_fornecedor
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.nome_produto} ({self.codigo})'


class Estoque(Prime):
    produtos = models.ForeignKey(Produtos, on_delete=models.CASCADE, related_name="produtos")
    data_validade = models.DateField(null=True, blank=True)
    quantidade = models.IntegerField(default=0)
    lote = models.IntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Produto em Estoque"
        verbose_name_plural = "Produtos em Estoque"
        constraints = [
            models.UniqueConstraint(
                fields=['produtos', 'data_validade'],
                name='uniq_produto_validade_not_null',
                condition=Q(data_validade__isnull=False)
            )
        ]

    def save(self, *args, **kwargs):
        with transaction.atomic():
            base_qs = Estoque.objects.select_for_update().filter(produtos=self.produtos)

            # Fundir com mesmo produto + validade
            same_date = base_qs.filter(data_validade=self.data_validade)
            if self.pk:
                same_date = same_date.exclude(pk=self.pk)

            existente = same_date.first()
            if existente:
                existente.quantidade += self.quantidade or 0
                existente.ativo = existente.quantidade > 0  # atualiza ativo
                existente.save(update_fields=['quantidade', 'ativo'])
                return

            super().save(*args, **kwargs)

            # Atualiza ativo com base na quantidade
            if self.quantidade <= 0:
                self.ativo = False
            else:
                self.ativo = True
            super().save(update_fields=['ativo'])

            # Renumera lotes por validade
            ordenados = base_qs.order_by(
                Case(When(data_validade__isnull=True, then=1), default=0, output_field=IntegerField()),
                'data_validade', 'pk'
            )
            for i, item in enumerate(ordenados, start=1):
                if item.lote != i:
                    Estoque.objects.filter(pk=item.pk).update(lote=i)

    def __str__(self):
        validade_fmt = self.data_validade.strftime('%d/%m/%Y') if self.data_validade else "Sem validade"
        return f"{self.produtos.nome_produto} - Validade: {validade_fmt}"


class SaidaEstoque(Prime):
    produto = models.ForeignKey(
        Produtos,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='saidas'
    )
    nome_produto = models.CharField(max_length=255, null=True)
    codigo_produto = models.CharField(max_length=100, null=True, blank=True)
    quantidade = models.IntegerField()
    data_saida = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    class Meta:
        verbose_name = "Saída de Estoque"
        verbose_name_plural = "Saídas de Estoque"

    def save(self, *args, **kwargs):
        """
        Ao salvar, se o produto estiver vinculado,
        copia seus dados primitivos (nome, código)
        para manter o histórico mesmo se o produto for excluído depois.
        """
        if self.produto and not self.nome_produto:
            self.nome_produto = self.produto.nome_produto
            self.codigo_produto = self.produto.codigo
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nome_produto} - {self.quantidade} un. (Saída)"


from django.utils.timezone import now


class DinheiroCaixa(Prime):
    usuario = models.ForeignKey(User, on_delete=models.CASCADE)
    valor_inicial = models.DecimalField(max_digits=10, decimal_places=2, default=0, null=True)
    data_abertura = models.DateField(default=now)

    class Meta:
        verbose_name = "Caixa do Dia"
        verbose_name_plural = "Caixas do Dia"

    def __str__(self):
        return f"Caixa de {self.usuario.username} - {self.data_abertura} (R$ {self.valor_inicial})"


class Venda(Prime):
    FORMAS_PAGAMENTO = [
        ('pix', 'PIX'),
        ('cartao_credito', 'Cartão de Crédito'),
        ('cartao_debito', 'Cartão de Débito'),
        ('dinheiro', 'Dinheiro'),
        ('pix_qrcode', 'Pix (QR code)'),
        ('pendente', 'Pendente'),
    ]

    usuario = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    forma_pagamento = models.CharField(max_length=20, choices=FORMAS_PAGAMENTO, default='pix')
    valor_bruto = models.DecimalField(decimal_places=2, max_digits=10, null=True)  # ✅ valor antes da taxa
    desconto_total = models.DecimalField(decimal_places=2, max_digits=10, default=0)
    taxa = models.DecimalField(decimal_places=2, max_digits=5, default=0)  # opcional, para registrar taxa aplicada
    valor_liquido = models.DecimalField(decimal_places=2, max_digits=10, null=True)  # valor recebido
    data = models.DateTimeField(auto_now_add=True)
    nome_cliente = models.CharField(max_length=90, null=True, blank=True)

    class Meta:
        verbose_name = "Venda"
        verbose_name_plural = "Vendas"

    def __str__(self):
        return f"Venda #{self.id} - {self.usuario.username if self.usuario else 'Anônimo'} - R$ {self.valor_liquido or 0:.2f}"


class ItemVenda(models.Model):
    venda = models.ForeignKey(Venda, on_delete=models.CASCADE, related_name='itens')
    produto = models.ForeignKey(Produtos, on_delete=models.CASCADE, related_name='itens_venda')
    quantidade = models.PositiveIntegerField(default=1)
    valor_unitario = models.DecimalField(max_digits=10, decimal_places=2)
    valor_total = models.DecimalField(max_digits=10, decimal_places=2)
    desconto = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        verbose_name = "Item de Venda"
        verbose_name_plural = "Itens de Venda"


class CategoriaDespesas(Prime):
    nome = models.CharField(max_length=50, unique=True)

    class Meta:
        verbose_name = "Categoria de Despesa"
        verbose_name_plural = "Categorias de Despesas"

    def __str__(self):
        return self.nome


class Despesa(Prime):
    categoria = models.ForeignKey(
        CategoriaDespesas,
        on_delete=models.SET_NULL,
        null=True,
        related_name="despesas"
    )
    descricao = models.CharField(max_length=255, blank=True, null=True)
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    data = models.DateField(null=True)

    class Meta:
        verbose_name = "Despesa"
        verbose_name_plural = "Despesas"

    def __str__(self):
        return f"{self.descricao or 'Despesa'} - R$ {self.valor}"


from django.db import models
from django.db.models import Sum
import datetime

from django.db import models
from datetime import datetime
from django.db.models import Sum


class FinanceiroMes(Prime):
    mes = models.PositiveSmallIntegerField()
    ano = models.PositiveSmallIntegerField()

    total_liquido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_ganho_potencial = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_despesas = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    lucro_liquido = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    lucro_potencial = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        unique_together = ('mes', 'ano')
        ordering = ['-ano', '-mes']
        verbose_name = "Fechamento Financeiro Mensal"
        verbose_name_plural = "Fechamentos Financeiros Mensais"

    def __str__(self):
        meses = [
            "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ]
        return f"{meses[self.mes - 1]} / {self.ano}"

    @classmethod
    def gerar_ou_atualizar(cls, mes=None, ano=None):
        from core.models import Venda, ItemVenda, Despesa  # evite import circular

        now = datetime.now()
        mes = mes or now.month
        ano = ano or now.year

        obj, _ = cls.objects.get_or_create(mes=mes, ano=ano)

        vendas = Venda.objects.filter(data__year=ano, data__month=mes)
        total_liquido = vendas.aggregate(Sum('valor_liquido'))['valor_liquido__sum'] or 0

        itens = ItemVenda.objects.filter(venda__in=vendas).select_related('produto')
        total_ganho_potencial = sum(
            (item.produto.ganho_potencial or 0) * item.quantidade for item in itens
        )

        despesas = Despesa.objects.filter(data__year=ano, data__month=mes)
        total_despesas = despesas.aggregate(Sum('valor'))['valor__sum'] or 0

        obj.total_liquido = total_liquido
        obj.total_ganho_potencial = total_ganho_potencial
        obj.total_despesas = total_despesas
        obj.lucro_liquido = total_liquido - total_despesas
        obj.lucro_potencial = total_ganho_potencial - total_despesas
        obj.save()

        return obj
