from django.db.models import Q, Case, When, IntegerField, Sum

from django.core.paginator import Paginator

from .forms import ProdutoForm, EstoqueForm
from .models import Produtos, CategoriaProduto, Estoque, DinheiroCaixa

from django.utils.timezone import now
from django.views.decorators.http import require_POST

from django.contrib.auth import authenticate, login

from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from .models import CategoriaDespesas


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dash_vendas')  # redireciona se já logado

    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:  # <-- remove 'and user.is_staff'
            login(request, user)
            return redirect('vender')  # agora qualquer usuário logado pode ir
        else:
            messages.error(request, "Usuário ou senha inválidos")

    return render(request, "login.html")


from django.utils import timezone


def abater_estoque(produto: Produtos, quantidade: int) -> int:
    """
    Abate a quantidade do produto no estoque (por lote mais antigo).
    Remove estoques zerados e cria/atualiza SaidaEstoque do mesmo dia.
    Retorna o total abatido.
    """
    estoque_total = (
            Estoque.objects.filter(produtos=produto).aggregate(total=Sum("quantidade"))["total"] or 0
    )
    if estoque_total <= 0:
        return 0

    quantidade_restante = min(quantidade, estoque_total)
    total_abatido = 0
    hoje = timezone.localdate()

    with transaction.atomic():
        estoques = (
            Estoque.objects.select_for_update()
            .filter(produtos=produto, quantidade__gt=0)
            .order_by("lote", "pk")
        )

        for estoque_item in estoques:
            if quantidade_restante <= 0:
                break

            abate = min(estoque_item.quantidade, quantidade_restante)
            estoque_item.quantidade -= abate
            estoque_item.save(update_fields=["quantidade"])
            quantidade_restante -= abate
            total_abatido += abate

            # 🔥 Remove estoque se zerar
            if estoque_item.quantidade <= 0:
                estoque_item.delete()

        # 🔥 Gera ou atualiza registro de Saída (sem usar __date)
        if total_abatido > 0:
            saida = SaidaEstoque.objects.filter(
                produto=produto, data_saida__date=hoje
            ).first()

            if saida:
                saida.quantidade += total_abatido
                saida.save(update_fields=["quantidade"])
            else:
                SaidaEstoque.objects.create(
                    produto=produto,
                    data_saida=timezone.now(),
                    quantidade=total_abatido,
                    nome_produto=produto.nome_produto,
                    codigo_produto=produto.codigo,
                )

    return total_abatido


@csrf_exempt
def finalizar_venda(request):
    if request.method != "POST":
        return JsonResponse({"sucesso": False, "erro": "Método inválido"})

    try:
        dados = json.loads(request.body)
        carrinho = dados.get("carrinho", [])
        forma_pagamento = dados.get("forma_pagamento")
        desconto_input = Decimal(dados.get("desconto", 0))
        valor_pago = Decimal(dados.get("valor_pago", 0))
        usuario = request.user if request.user.is_authenticated else None
        nome_cliente = dados.get("nome_cliente")

        if not carrinho:
            return JsonResponse({"sucesso": False, "erro": "Carrinho vazio"})

        TAXAS = {
            "cartao_debito": Decimal("1.99"),
            "cartao_credito": Decimal("1.99"),
            "pix_qrcode": Decimal("4.99"),
            "pix": Decimal("0"),
            "dinheiro": Decimal("0"),
            "pendente": Decimal("0"),
        }

        valor_bruto = sum(Decimal(str(i["preco"])) * int(i["qtd"]) for i in carrinho)
        desconto_total = desconto_input
        valor_com_desconto = max(valor_bruto - desconto_total, 0)
        taxa_percentual = TAXAS.get(forma_pagamento, Decimal("0"))
        taxa_aplicada = (valor_com_desconto * taxa_percentual / 100).quantize(Decimal("0.01"))
        valor_liquido = max(valor_com_desconto - taxa_aplicada, 0)

        troco = Decimal("0.00")
        if forma_pagamento == "dinheiro" and valor_pago > valor_liquido:
            troco = (valor_pago - valor_liquido).quantize(Decimal("0.01"))

        with transaction.atomic():
            estoque_insuficiente = []

            # 1️⃣ Abate estoque
            for item in carrinho:
                produto = Produtos.objects.get(id=item["id"])
                categoria = (produto.categoria.nome_categoria or "").lower()
                qtd_solicitada = int(item.get("qtd", 0))

                # Produtos normais: abate estoque
                if categoria not in ["combos", "doses", "fracionados"]:
                    abatido = abater_estoque(produto, qtd_solicitada)
                    if abatido < qtd_solicitada:
                        estoque_insuficiente.append(
                            f"{produto.nome_produto} (Disponível: {abatido}, Necessário: {qtd_solicitada})"
                        )

                # Complementos (gelo sempre obrigatório para combos/doses)
                # 🧊 Complementos de gelo (padrão diferente para combos)
                gelo_items = [comp for comp in item.get("complementos", []) if comp["tipo"] == "gelo"]
                for gelo in gelo_items:
                    produto_comp = Produtos.objects.get(id=gelo["id"])

                    # 🔥 Se for combo e não tiver quantidade definida maior, usa padrão de 5
                    if categoria == "combos":
                        qtd_comp = int(gelo.get("qtd", 0)) or 5
                    else:
                        qtd_comp = int(gelo.get("qtd", 0))

                    abatido = abater_estoque(produto_comp, qtd_comp)
                    if abatido < qtd_comp:
                        estoque_insuficiente.append(
                            f"{produto_comp.nome_produto} (Disponível: {abatido}, Necessário: {qtd_comp})"
                        )

                # Red Bull (opcional)
                rb_items = [comp for comp in item.get("complementos", []) if comp["tipo"] == "rb"]
                for rb in rb_items:
                    produto_comp = Produtos.objects.get(id=rb["id"])
                    qtd_comp = int(rb.get("qtd", 0))  # ✅ corrigido
                    abatido = abater_estoque(produto_comp, qtd_comp)
                    if abatido < qtd_comp:
                        estoque_insuficiente.append(
                            f"{produto_comp.nome_produto} (Disponível: {abatido}, Necessário: {qtd_comp})"
                        )

            if estoque_insuficiente:
                raise ValueError("Estoque insuficiente: " + ", ".join(estoque_insuficiente))

            # 2️⃣ Cria venda
            venda = Venda.objects.create(
                usuario=usuario,
                forma_pagamento=forma_pagamento,
                valor_bruto=valor_bruto,
                desconto_total=desconto_total,
                taxa=taxa_aplicada,
                valor_liquido=valor_liquido,
                nome_cliente=nome_cliente,
            )

            # 3️⃣ Cria itens da venda
            for item in carrinho:
                produto = Produtos.objects.get(id=item["id"])
                categoria = (produto.categoria.nome_categoria or "").lower()
                qtd_solicitada = int(item.get("qtd", 0))
                preco_unitario = Decimal(str(item["preco"]))
                valor_item_bruto = preco_unitario * qtd_solicitada
                desconto_item = round((valor_item_bruto / valor_bruto) * desconto_total, 2) if valor_bruto > 0 else 0
                valor_item_liquido = max(valor_item_bruto - desconto_item, 0)

                # Item principal
                ItemVenda.objects.create(
                    venda=venda,
                    produto=produto,
                    quantidade=qtd_solicitada,
                    valor_unitario=preco_unitario,
                    valor_total=valor_item_liquido,
                    desconto=desconto_item,
                )

                # 🧾 Complementos: gelo + Red Bull
                for comp in item.get("complementos", []):
                    produto_comp = Produtos.objects.get(id=comp["id"])

                    if categoria == "combos" and comp["tipo"] == "gelo":
                        qtd_comp = int(comp.get("qtd", 0)) or 5
                    else:
                        qtd_comp = int(comp.get("qtd", 0))

                    ItemVenda.objects.create(
                        venda=venda,
                        produto=produto_comp,
                        quantidade=qtd_comp,
                        valor_unitario=produto_comp.preco_venda,
                        valor_total=produto_comp.preco_venda * qtd_comp,
                        desconto=0,
                    )

        return JsonResponse({
            "sucesso": True,
            "venda_id": venda.id,
            "valor_liquido": float(valor_liquido),
            "valor_bruto": float(valor_bruto),
            "troco": float(troco),
        })

    except ValueError as ve:
        return JsonResponse({"sucesso": False, "erro": str(ve)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JsonResponse({"sucesso": False, "erro": str(e)})


from django.db.models import Q, Case, When, IntegerField

from collections import defaultdict


def vender(request):
    hoje = timezone.localdate()
    busca = request.GET.get("busca", "").strip()
    categoria_filtro = request.GET.get("categoria", "").strip()
    pagina = request.GET.get("pagina", 1)

    # --- FILTRO DE PRODUTOS ---
    produtos_query = Produtos.objects.select_related("categoria").all()
    if busca:
        produtos_query = produtos_query.filter(
            Q(nome_produto__icontains=busca) | Q(codigo__icontains=busca)
        )
    if categoria_filtro:
        produtos_query = produtos_query.filter(
            categoria__nome_categoria__iexact=categoria_filtro
        )
    produtos_query = produtos_query.order_by(
        Case(
            When(categoria__nome_categoria="Doses", then=0),
            default=1,
            output_field=IntegerField()
        ),
        'nome_produto'
    )
    paginator = Paginator(produtos_query, 9)
    produtos_paginados = paginator.get_page(pagina)

    # --- Dados auxiliares ---
    gelos = Produtos.objects.filter(categoria__nome_categoria__iexact="Gelos")
    redbulls = Produtos.objects.filter(
        categoria__nome_categoria__iexact="Energeticos",
        nome_produto__icontains="redbull"
    ).order_by("nome_produto")
    categorias = CategoriaProduto.objects.values_list("nome_categoria", flat=True).order_by("nome_categoria")
    categorias_com_gelo = ["Doses", "Combos"]

    # --- Vendas do dia ---
    vendas_dia = Venda.objects.filter(data__date=hoje).prefetch_related('itens', 'itens__produto').order_by("-data")

    # --- Renderiza diretamente os objetos para o template ---
    return render(request, "vender.html", {
        "produtos_paginados": produtos_paginados,
        "vendas_dia": vendas_dia,
        "categorias": categorias,
        "gelos": list(gelos),
        "redbulls": list(redbulls),
        "filtro_busca": busca,
        "filtro_categoria": categoria_filtro,
        "categorias_com_gelo": categorias_com_gelo,
    })


from decimal import Decimal, ROUND_HALF_UP
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404
import json
from .models import Venda

# TAXAS em porcentagem
TAXAS = {
    "cartao_debito": Decimal("1.99"),
    "cartao_credito": Decimal("1.99"),
    "pix_qrcode": Decimal("4.99"),
    "pix": Decimal("0"),
    "dinheiro": Decimal("0"),
}


@csrf_exempt
def quitar_pendente(request):
    if request.method != "POST":
        return JsonResponse({"sucesso": False, "erro": "Método inválido"})

    try:
        dados = json.loads(request.body)
        venda_id = dados.get("venda_id")
        nova_forma_pagamento = dados.get("nova_forma_pagamento")

        if not venda_id or not nova_forma_pagamento:
            return JsonResponse({"sucesso": False, "erro": "Dados incompletos"})

        venda = get_object_or_404(Venda, id=venda_id)

        if venda.forma_pagamento != "pendente":
            return JsonResponse({"sucesso": False, "erro": "Venda não é pendente"})

        # --- TAXAS COMO DECIMAL ---
        TAXAS = {
            "cartao_debito": Decimal("1.99"),
            "cartao_credito": Decimal("1.99"),
            "pix_qrcode": Decimal("4.99"),
            "pix": Decimal("0"),
            "dinheiro": Decimal("0"),
        }

        # Valores como Decimal
        valor_bruto = Decimal(str(venda.valor_bruto or "0.00"))
        desconto_total = Decimal(str(venda.desconto_total or "0.00"))
        valor_com_desconto = max(valor_bruto - desconto_total, Decimal("0.00"))

        taxa_percentual = TAXAS.get(nova_forma_pagamento, Decimal("0.00"))

        # Calcula a taxa aplicada e valor líquido
        taxa_aplicada = (valor_com_desconto * taxa_percentual / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        valor_liquido = (valor_com_desconto - taxa_aplicada).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Atualiza venda
        venda.forma_pagamento = nova_forma_pagamento
        venda.taxa = taxa_aplicada
        venda.valor_liquido = valor_liquido
        venda.save()

        return JsonResponse({
            "sucesso": True,
            "venda_id": venda.id,
            "nova_forma_pagamento": venda.forma_pagamento,
            "valor_liquido": str(venda.valor_liquido),
            "taxa": str(venda.taxa)
        })

    except Venda.DoesNotExist:
        return JsonResponse({"sucesso": False, "erro": "Venda não encontrada"})
    except Exception as e:
        return JsonResponse({"sucesso": False, "erro": str(e)})


# 🔹 Função auxiliar para status da validade
from datetime import date, timedelta


def _status_validade(data_validade):
    if not data_validade:
        return "verde"
    hoje = date.today()
    if data_validade < hoje:
        return "expirado"
    elif data_validade <= hoje + timedelta(days=5):
        return "vermelho"
    elif data_validade <= hoje + timedelta(days=15):
        return "amarelo"
    return "verde"


def produtos(request):
    busca = request.GET.get('busca', '')
    categoria_id = request.GET.get('categoria', '')
    pagina = request.GET.get('page', 1)  # Página atual

    produtos_qs = Produtos.objects.all().order_by('nome_produto')  # ou qualquer campo desejado

    if busca:
        produtos_qs = produtos_qs.filter(
            Q(nome_produto__icontains=busca) | Q(codigo__icontains=busca)
        )
    if categoria_id:
        produtos_qs = produtos_qs.filter(categoria_id=categoria_id)

    categorias = CategoriaProduto.objects.all()

    # Paginação
    paginator = Paginator(produtos_qs, 12)  # 12 produtos por página
    produtos_paginados = paginator.get_page(pagina)

    # Checar se há despesas
    despesas_vazias = not Despesa.objects.exists()

    if request.method == 'POST' and request.headers.get('x-requested-with') == 'XMLHttpRequest':
        form = ProdutoForm(request.POST, request.FILES)
        if form.is_valid():
            produto = form.save()
            return JsonResponse({
                'success': True,
                'message': '✅ Produto adicionado com sucesso!',
                'produto': {
                    'id': produto.id,
                    'nome': produto.nome,
                    'codigo': produto.codigo,
                    'categoria': produto.categoria.nome_categoria,
                    'preco_venda': str(produto.preco_venda),
                    'preco_fornecedor': str(produto.preco_fornecedor),
                    'ganho': str(produto.ganho_potencial),
                    'imagem': produto.imagem.url if produto.imagem else None
                }
            })
        else:
            return JsonResponse({
                'success': False,
                'message': '❌ Erro ao salvar o produto. Este código já está registrado.'
            })

    form = ProdutoForm()
    return render(request, 'produtos.html', {
        'produtos': produtos_paginados,  # agora paginados
        'categorias': categorias,
        'categoria_id': categoria_id,
        'busca': busca,
        'form': form,
        'despesas_vazias': despesas_vazias,  # PASSANDO PARA O TEMPLATE
    })


from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .forms import ProdutoForm


@csrf_exempt
def cadastrar_produto(request):
    if request.method == "POST":
        form = ProdutoForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return JsonResponse({"success": True})
        return JsonResponse({"success": False, "error": form.errors})
    return JsonResponse({"success": False, "error": "Método inválido"})


from decimal import Decimal, InvalidOperation


@csrf_exempt
def editar_produto(request, pk):
    produto = get_object_or_404(Produtos, pk=pk)

    if request.method == "POST":
        try:
            nome_produto = request.POST.get("nome_produto")
            codigo = request.POST.get("codigo")
            categoria_id = request.POST.get("categoria")
            preco_fornecedor = request.POST.get("preco_fornecedor")
            preco_venda = request.POST.get("preco_venda")  # 👈 adicione isso

            if nome_produto:
                produto.nome_produto = nome_produto

            if codigo:
                produto.codigo = codigo

            if categoria_id:
                from .models import CategoriaProduto
                categoria = get_object_or_404(CategoriaProduto, id=categoria_id)
                produto.categoria = categoria

            # 👇 converte valores numéricos para Decimal
            try:
                if preco_fornecedor:
                    produto.preco_fornecedor = Decimal(preco_fornecedor)
                else:
                    produto.preco_fornecedor = Decimal("0.00")

                if preco_venda:
                    produto.preco_venda = Decimal(preco_venda)
                else:
                    produto.preco_venda = Decimal("0.00")
            except InvalidOperation:
                return JsonResponse({"sucesso": False, "erros": "Valor inválido de preço."})

            # 👇 imagem
            if request.FILES.get("imagem"):
                produto.imagem = request.FILES["imagem"]

            produto.save()

            return JsonResponse({"sucesso": True})

        except Exception as e:
            return JsonResponse({"sucesso": False, "erros": str(e)})

    else:
        data = {
            "codigo": produto.codigo,
            "nome_produto": produto.nome_produto,
            "preco_venda": str(produto.preco_venda),
            "categoria": produto.categoria.id,
            "preco_fornecedor": str(produto.preco_fornecedor) if produto.preco_fornecedor else "",
            "imagem": produto.imagem.url if produto.imagem else "",
        }
        return JsonResponse({"produto": data})


@csrf_exempt
def excluir_produto(request, produto_id):
    if request.method == 'POST':  # trocado DELETE por POST
        try:
            produto = Produtos.objects.get(id=produto_id)
            produto.delete()
            return JsonResponse({'success': True, 'message': '✅ Produto excluído com sucesso!'})
        except Produtos.DoesNotExist:
            return JsonResponse({'success': False, 'message': '❌ Produto não encontrado.'}, status=404)
    return JsonResponse({'success': False, 'message': 'Método inválido.'}, status=405)


from django.http import JsonResponse
from django.db.models import Q


def baixa_geral_estoque(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            produtos = data.get("produtos", [])

            for item in produtos:
                estoque = Estoque.objects.get(id=item["id"])
                qtd_remover = int(item.get("qtd_remover", 0))

                if qtd_remover > 0 and qtd_remover <= estoque.quantidade:
                    estoque.quantidade -= qtd_remover
                    estoque.save()

            # Buscar estoque atualizado (sem combos e doses, quantidade > 0)
            estoque_list = (
                Estoque.objects
                .select_related("produtos")
                .exclude(
                    Q(produtos__categoria__nome_categoria__iexact="doses") |
                    Q(produtos__categoria__nome_categoria__iexact="combos")
                )
                .filter(quantidade__gt=0)
                .order_by("lote", "quantidade")  # menores lotes e menores quantidades primeiro
                .values(
                    "id",
                    "produtos__id",
                    "produtos__nome_produto",
                    "produtos__codigo",
                    "produtos__imagem",
                    "quantidade",
                    "lote",
                    "data_validade"
                )
            )

            return JsonResponse({
                "success": True,
                "estoque_atualizado": list(estoque_list)
            })
        except Exception as e:
            return JsonResponse({"success": False, "message": str(e)})

    return JsonResponse({"success": False, "message": "Método não permitido"})


@csrf_exempt
def estoque_adicao_massa(request):
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            itens = data.get("itens", [])

            for item in itens:
                produto = Produtos.objects.get(id=item["id"])
                quantidade = int(item["quantidade"])
                validade = item.get("validade")
                if validade:
                    validade = datetime.strptime(validade, "%Y-%m-%d").date()

                # Procura estoque existente
                if validade:
                    estoque_existente = Estoque.objects.filter(
                        produtos=produto,
                        data_validade=validade
                    ).first()
                else:
                    estoque_existente = Estoque.objects.filter(
                        produtos=produto,
                        data_validade__isnull=True
                    ).first()

                if estoque_existente:
                    # Soma quantidade existente
                    estoque_existente.quantidade += quantidade
                    estoque_existente.save()
                else:
                    # Cria novo registro
                    Estoque.objects.create(
                        produtos=produto,
                        quantidade=quantidade,
                        data_validade=validade if validade else None
                    )

            return JsonResponse({"sucesso": True})
        except Exception as e:
            return JsonResponse({"sucesso": False, "erro": str(e)})

    return JsonResponse({"sucesso": False, "erro": "Método inválido"})


from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def baixa_unica(request):
    if request.method != "POST":
        return JsonResponse({"error": "Método inválido."}, status=405)

    try:
        data = json.loads(request.body)
        produto_id = data.get("produto_id")
        qtd = int(data.get("quantidade"))

        estoque_item = Estoque.objects.select_related("produtos").filter(id=produto_id).first()
        if not estoque_item:
            return JsonResponse({"error": "Produto não encontrado."}, status=404)

        if estoque_item.quantidade < qtd:
            return JsonResponse({"error": "Quantidade insuficiente no estoque."}, status=400)

        produto = estoque_item.produtos

        # Reduz a quantidade do estoque
        estoque_item.quantidade -= qtd
        estoque_item.save()

        # Verifica se já existe saída para este produto hoje
        from django.utils import timezone
        hoje = timezone.now().date()

        saida, created = SaidaEstoque.objects.get_or_create(
            produto=produto,
            nome_produto=produto.nome_produto,
            codigo_produto=produto.codigo,
            data_saida__date=hoje,
            defaults={"quantidade": 0}
        )

        # Atualiza a quantidade da saída
        saida.quantidade += qtd
        saida.save()

        # Apaga o estoque se zerar
        if estoque_item.quantidade <= 0:
            estoque_item.delete()

        return JsonResponse({"success": True, "saida_id": saida.id})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def estoque(request):
    # Obtém todas as categorias para o filtro
    categorias = CategoriaProduto.objects.exclude(
        Q(nome_categoria__iexact="doses") | Q(nome_categoria__iexact="combos")
    ).order_by('nome_categoria')

    # ======= LIMPEZA AUTOMÁTICA =======
    Estoque.objects.filter(quantidade__lte=0).delete()

    # ========== FORM DE CADASTRO ==========
    if request.method == "POST":
        estoque_form = EstoqueForm(request.POST)
        if estoque_form.is_valid():
            estoque_form.save()
            return redirect("estoque")
    else:
        estoque_form = EstoqueForm()

    # ========== DEFINIÇÕES E FILTROS INICIAIS ==========
    busca = request.GET.get("busca", "")
    validade = request.GET.get("validade", "")
    categoria_id = request.GET.get("categoria", "")

    # CRUCIAL: 'hoje' deve ser definida aqui, antes de qualquer lógica que a utilize.
    hoje = now().date()

    estoque_qs = (
        Estoque.objects
        .select_related("produtos", "produtos__categoria")  # Adicionado 'produtos__categoria' para otimização do filtro
        .exclude(
            Q(produtos__categoria__nome_categoria__iexact="doses") |
            Q(produtos__categoria__nome_categoria__iexact="combos")
        )
        .order_by("lote", "quantidade")
    )

    # --- JSONs (MANTIDOS E INALTERADOS) ---
    todos_produtos = Produtos.objects.exclude(
        Q(categoria__nome_categoria__iexact="doses") | Q(categoria__nome_categoria__iexact="combos")
    ).order_by('nome_produto')

    produtos_list = list(todos_produtos.values(
        "id", "nome_produto", "codigo", "imagem"
    ))
    todos_produtos_json = json.dumps(produtos_list, cls=DjangoJSONEncoder)

    estoque_list = (
        Estoque.objects
        .select_related("produtos")
        .exclude(
            Q(produtos__categoria__nome_categoria__iexact="doses") |
            Q(produtos__categoria__nome_categoria__iexact="combos")
        )
        .filter(quantidade__gt=0)
        .order_by("lote", "quantidade")
        .values(
            "id", "produtos__id", "produtos__nome_produto", "produtos__codigo",
            "produtos__imagem", "quantidade", "lote", "data_validade"
        )
    )
    estoque_json = json.dumps(list(estoque_list), cls=DjangoJSONEncoder)
    # -------------------------------------

    # ========== FILTRO DE BUSCA ==========
    if busca:
        estoque_qs = estoque_qs.filter(
            Q(produtos__nome_produto__icontains=busca) |
            Q(produtos__codigo__icontains=busca)
        )

    # ========== FILTRO DE CATEGORIA (Corrigido o escopo/indentação) ==========
    if categoria_id:
        # Filtra o estoque pelo ID da categoria do produto
        estoque_qs = estoque_qs.filter(produtos__categoria__id=categoria_id)

    # ========== FILTRO DE VALIDADE (Corrigido o escopo/indentação) ==========
    if validade == "proximo":
        limite = hoje + timedelta(days=5)
        estoque_qs = estoque_qs.filter(data_validade__range=(hoje, limite))
    elif validade == "promocao":
        inicio = hoje - timedelta(days=12)
        fim = hoje + timedelta(days=6)
        estoque_qs = estoque_qs.filter(data_validade__range=(inicio, fim))

    # ========== ANOTAÇÃO DE BORDA ==========
    estoque_com_borda = []
    for item in estoque_qs:
        # 'hoje' está definida e disponível aqui!
        if not item.data_validade:
            borda = "borda-cinza"
        elif item.data_validade < hoje:
            borda = "borda-preta"
        elif item.data_validade <= hoje + timedelta(days=15):
            borda = "borda-vermelha"
        else:
            borda = "borda-verde"

        estoque_com_borda.append({
            "obj": item,
            "borda": borda,
        })

    # ========== PAGINAÇÃO ==========
    paginator = Paginator(estoque_com_borda, 9)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    # ========== CONTEXTO ==========
    contexto = {
        "form": estoque_form,
        "estoque": page_obj,
        "busca": busca,
        "validade": validade,
        "categorias": categorias,
        "categoria_id": categoria_id,
        "todos_produtos_json": todos_produtos_json,
        "estoque_json": estoque_json,

    }

    return render(request, "estoque.html", contexto)


def profile(request):
    return render(request, 'profile.html')


from django.contrib.auth.decorators import login_required


@login_required(login_url='login')  # redireciona para login se não estiver autenticado
def lead_dash(request):
    if not request.user.is_staff:
        # Se não for staff, mostra mensagem de permissão negada
        return render(request, 'dashboard.html', {
            'acesso_negado': True
        })
    return render(request, 'dashboard.html')


def painel(request):
    return render(request, 'painel.html')


from django.core.serializers.json import DjangoJSONEncoder


def get_venda_itens(request, venda_id):
    try:
        venda = Venda.objects.get(id=venda_id)
    except Venda.DoesNotExist:
        return JsonResponse({"error": "Venda não encontrada"}, status=404)

    itens = venda.itens.select_related("produto").all()

    data = []
    for item in itens:
        data.append({
            "produto": item.produto.nome_produto,
            "imagem": item.produto.imagem.url if item.produto.imagem else None,
            "quantidade": item.quantidade,
            "valor_unitario": float(item.valor_unitario),
            "valor_total": float(item.valor_total),
            "desconto": float(item.desconto),
            "preco_catalogo": float(item.produto.preco_venda),
        })

    return JsonResponse({"venda_id": venda.id, "itens": data})


from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def quitar_venda(request, venda_id):
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "Método inválido"})

    try:
        venda = Venda.objects.get(id=venda_id)
    except Venda.DoesNotExist:
        return JsonResponse({"success": False, "error": "Venda não encontrada"})

    try:
        data = json.loads(request.body)
        nova_forma = data.get("forma_pagamento")
        if not nova_forma or nova_forma == "pendente":
            return JsonResponse({"success": False, "error": "Forma de pagamento inválida"})

        # Valores como Decimal
        valor_bruto = Decimal(str(venda.valor_bruto or "0.00"))
        desconto_total = Decimal(str(venda.desconto_total or "0.00"))
        valor_com_desconto = max(valor_bruto - desconto_total, Decimal("0.00"))

        # Taxa baseada na forma de pagamento
        taxa_percentual = TAXAS.get(nova_forma, Decimal("0.00"))
        taxa_aplicada = (valor_com_desconto * taxa_percentual / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Valor líquido após taxa
        valor_liquido = (valor_com_desconto - taxa_aplicada).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Atualiza venda
        venda.forma_pagamento = nova_forma
        venda.taxa = taxa_aplicada
        venda.valor_liquido = valor_liquido
        venda.save()

        return JsonResponse({
            "success": True,
            "venda_id": venda.id,
            "nova_forma_pagamento": venda.forma_pagamento,
            "valor_liquido": str(venda.valor_liquido),
            "taxa": str(venda.taxa)
        })

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


from django.db.models import Sum
from django.core.paginator import Paginator
from django.shortcuts import render
from datetime import datetime
import json
from django.db.models import Q


# Importe Venda e outros modelos/módulos necessários
from datetime import datetime, date
from django.db.models import Sum
from django.shortcuts import render
from django.core.paginator import Paginator  # Adicione esta importação
import json


# Assuma que Venda.FORMAS_PAGAMENTO e Venda estão definidos em outro lugar.

def dash_vendas(request):
    # Base QuerySet
    vendas_list = Venda.objects.all().order_by("-data")

    # --- Recebe filtros ---
    periodo_submetido = request.GET.get("periodo")
    status_pagamento = request.GET.get("status_pagamento", "todos")
    forma_pagamento_filtro = request.GET.get("forma_pagamento", "")

    data_inicio_obj = data_fim_obj = None
    data_inicio_context = data_fim_context = ""  # Variáveis para o template (iniciais vazias)

    # --- LÓGICA DE DATAS CORRIGIDA (com foco em 'periodo') ---

    # 1. Primeira Carga (URL sem ?periodo=...): Define o padrão como hoje, mas não preenche o contexto.
    if periodo_submetido is None:
        hoje = datetime.today().date()
        data_inicio_obj = hoje
        data_fim_obj = hoje
        # Não preenche data_inicio_context e data_fim_context, para que os inputs fiquem vazios.

    # 2. Filtragem Ativa (periodo contém datas): Tenta extrair e aplicar o filtro.
    elif periodo_submetido:
        partes = periodo_submetido.split(" - ")
        if len(partes) == 2:
            try:
                data_inicio_obj = datetime.strptime(partes[0].strip(), "%d/%m/%Y").date()
                data_fim_obj = datetime.strptime(partes[1].strip(), "%d/%m/%Y").date()
            except ValueError:
                pass  # Em caso de erro, data_inicio_obj e data_fim_obj continuam None

        # Se a conversão foi bem-sucedida, preenche o contexto para REPOULAR o formulário
        if data_inicio_obj and data_fim_obj:
            data_inicio_context = data_inicio_obj.strftime("%d/%m/%Y")
            data_fim_context = data_fim_obj.strftime("%d/%m/%Y")

    # 3. Filtro Limpo (periodo é string vazia ''): data_inicio_obj e data_fim_obj permanecem None/Vazios.
    #    Neste caso, o filtro de data é ignorado e os inputs do template ficam vazios.

    # 4. Aplica o filtro de data (se objetos de data válidos existirem)
    if data_inicio_obj and data_fim_obj:
        if data_inicio_obj > data_fim_obj:
            data_inicio_obj, data_fim_obj = data_fim_obj, data_inicio_obj  # Garante a ordem correta

        vendas_list = vendas_list.filter(
            data__date__gte=data_inicio_obj,
            data__date__lte=data_fim_obj
        )
        dias_intervalo = (data_fim_obj - data_inicio_obj).days + 1
    else:
        dias_intervalo = None

    # --- FIM DA LÓGICA DE DATAS ---

    # --- DEFINE A LISTA PARA EXIBIÇÃO E SOMA DOS TOTAIS ---

    # Aplica filtros de status/forma (mantido como original, interage com o filtro de data acima)
    if forma_pagamento_filtro:
        vendas_list = vendas_list.filter(forma_pagamento=forma_pagamento_filtro)
    else:
        if status_pagamento == "pagas":
            vendas_list = vendas_list.exclude(forma_pagamento="pendente")
        elif status_pagamento == "pendentes":
            vendas_list = vendas_list.filter(forma_pagamento="pendente")

    # Filtra para totais apenas vendas pagas
    vendas_totais = vendas_list.exclude(forma_pagamento="pendente")

    # --- Totais filtrados (somente vendas pagas) ---
    total_bruto = vendas_totais.aggregate(Sum("valor_bruto"))["valor_bruto__sum"] or 0
    total_liquido = vendas_totais.aggregate(Sum("valor_liquido"))["valor_liquido__sum"] or 0

    # --- Paginação ---
    paginator = Paginator(vendas_list, 30)
    page_number = request.GET.get("page")
    vendas = paginator.get_page(page_number)

    # --- Formas de Pagamento para o <select> ---
    formas_pagamento_choices = Venda.FORMAS_PAGAMENTO

    # --- Serializa itens (mantido como original) ---
    vendas_itens_json = {}
    for venda_obj in vendas_list:
        itens = []
        for item in venda_obj.itens.select_related('produto').all():
            itens.append({
                "produto": item.produto.nome_produto,
                "quantidade": item.quantidade,
                "valor_unitario": float(item.valor_unitario),
                "desconto": float(item.desconto),
                "valor_total": float(item.valor_total),
            })
        vendas_itens_json[venda_obj.id] = itens

    context = {
        "vendas": vendas,
        # ✅ data_inicio e data_fim virão vazias na primeira carga ou se o filtro for limpo.
        "data_inicio": data_inicio_context,
        "data_fim": data_fim_context,
        "dias_intervalo": dias_intervalo,
        "status_pagamento": status_pagamento,
        "forma_pagamento_filtro": forma_pagamento_filtro,
        "formas_pagamento_choices": formas_pagamento_choices,
        "total_bruto": total_bruto,
        "total_liquido": total_liquido,
        "vendas_itens_json": json.dumps(vendas_itens_json),
    }
    return render(request, "dash_vendas.html", context)


from django.db.models import Count
from datetime import datetime, timedelta
import json

from django.db.models.functions import TruncDate


def dash_vendas_graficos(request):
    periodo = request.GET.get("periodo", "")
    dia_semana = request.GET.get("dia_semana", "todos")

    data_inicio = data_fim = None
    vendas_query = Venda.objects.all()

    # filtro por período
    if periodo:
        partes = periodo.split(" - ")
        if len(partes) == 2:
            try:
                data_inicio = datetime.strptime(partes[0].strip(), "%d/%m/%Y").date()
                data_fim = datetime.strptime(partes[1].strip(), "%d/%m/%Y").date()
                vendas_query = vendas_query.filter(data__date__range=[data_inicio, data_fim])
            except ValueError:
                pass

    # filtro por dia da semana
    if dia_semana != "todos":
        semana_map = {
            "segunda": 0, "terca": 1, "quarta": 2,
            "quinta": 3, "sexta": 4, "sabado": 5, "domingo": 6
        }
        if dia_semana in semana_map:
            vendas_query = vendas_query.filter(data__week_day=semana_map[dia_semana] + 1)

    # --- GRÁFICO 1: VENDAS POR DIA ---
    vendas_por_dia = (
        vendas_query
        .annotate(day=TruncDate("data"))
        .values("day")
        .annotate(total=Count("id"))
        .order_by("day")
    )

    if vendas_por_dia:
        grafico1_labels = [v["day"].strftime("%d/%m/%Y") for v in vendas_por_dia]
        grafico1_values = [v["total"] for v in vendas_por_dia]
        mensagem = ""
    else:
        grafico1_labels = []
        grafico1_values = []
        mensagem = "Nenhuma venda encontrada no período selecionado."

    # --- GRÁFICO 2: FORMAS DE PAGAMENTO ---
    formas_map = {
        "cartao_credito": "Cartão de Crédito",
        "pix": "PIX",
        "boleto": "Boleto",
        "dinheiro": "Dinheiro",
        # adicione outras formas aqui
    }

    vendas_por_forma = (
        vendas_query
        .values("forma_pagamento")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    if vendas_por_forma:
        formas_labels = [formas_map.get(v["forma_pagamento"], v["forma_pagamento"].title().replace("_", " "))
                         for v in vendas_por_forma]
        formas_data = [v["total"] for v in vendas_por_forma]
        mensagem_pagamento = ""
    else:
        formas_labels = []
        formas_data = []
        mensagem_pagamento = "Nenhuma venda encontrada no período selecionado."

    # --- GRÁFICO 3: TOP USUÁRIOS EM VENDAS ---
    top_usuarios = (
        vendas_query
        .exclude(usuario__isnull=True)
        .values("usuario__username")
        .annotate(total_vendas=Sum("valor_liquido"))  # soma o valor líquido vendido
        .order_by("-total_vendas")[:10]  # pega top 10 usuários
    )

    if top_usuarios:
        top_labels = [v["usuario__username"] for v in top_usuarios]
        top_values = [float(v["total_vendas"]) for v in top_usuarios]
    else:
        top_labels = []
        top_values = []
    context = {
        # Gráfico 1
        "grafico_labels": json.dumps(grafico1_labels),
        "grafico_values": json.dumps(grafico1_values),
        "mensagem": mensagem,
        "data_inicio": data_inicio.strftime("%d/%m/%Y") if data_inicio else "",
        "data_fim": data_fim.strftime("%d/%m/%Y") if data_fim else "",
        "dia_semana": dia_semana,
        # Gráfico 2
        "formasLabels": json.dumps(formas_labels),
        "formasData": json.dumps(formas_data),
        "mensagem_pagamento": mensagem_pagamento,
        # Gráfico 3
        "topLabels": json.dumps(top_labels),
        "topValues": json.dumps(top_values),
    }

    return render(request, "dash_vendas_graf.html", context)


from django.db.models import F, Sum, DecimalField, ExpressionWrapper
import json


def dashboard_estoque(request):
    categorias = Estoque.objects.select_related('produtos__categoria', 'produtos') \
        .values_list('produtos__categoria__nome_categoria', flat=True) \
        .distinct()

    categoria_selecionada = request.GET.get('categoria', 'Todos')

    produtos_query = Estoque.objects.select_related('produtos__categoria', 'produtos').filter(quantidade__gt=0)
    if categoria_selecionada != 'Todos':
        produtos_query = produtos_query.filter(produtos__categoria__nome_categoria=categoria_selecionada)

    produtos_query = produtos_query.order_by('produtos__nome_produto')

    chart_data = []
    for e in produtos_query:
        chart_data.append({
            "produto": e.produtos.nome_produto,
            "categoria": e.produtos.categoria.nome_categoria if e.produtos.categoria else 'Sem Categoria',
            "quantidade": e.quantidade,
            "lote": e.lote,
            "data_validade": e.data_validade.strftime("%d/%m/%Y") if e.data_validade else '-',
            "imagem": e.produtos.imagem.url if e.produtos.imagem else '',
            "criacao": e.criacao.strftime("%d/%m/%Y %H:%M") if e.criacao else '-'
        })

    chart_data_json = json.dumps(chart_data) if chart_data else None

    # 🧮 Cálculos financeiros do estoque
    estoque = Estoque.objects.select_related('produtos')

    ganho_potencial_estoque = estoque.aggregate(
        total=Sum(
            ExpressionWrapper(
                F('quantidade') * F('produtos__ganho_potencial'),
                output_field=DecimalField()
            )
        )
    )['total'] or 0

    valor_parado_estoque = estoque.aggregate(
        total=Sum(
            ExpressionWrapper(
                F('quantidade') * F('produtos__preco_fornecedor'),
                output_field=DecimalField()
            )
        )
    )['total'] or 0

    context = {
        "chart_data": chart_data_json,
        "categorias": categorias,
        "categoria_selecionada": categoria_selecionada,
        "ganho_potencial_estoque": round(ganho_potencial_estoque, 2),
        "valor_parado_estoque": round(valor_parado_estoque, 2),
    }

    return render(request, "dash_stock_grafico.html", context)


from .models import Despesa, FinanceiroMes
from .forms import DespesaForm


def financeiro_mensal(request):
    # Criação de nova despesa
    if request.method == 'POST':
        form = DespesaForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('balanco')
    else:
        form = DespesaForm()

    # Filtro mês/ano
    mes = int(request.GET.get('mes', datetime.now().month))
    ano = int(request.GET.get('ano', datetime.now().year))

    # Despesas do mês
    despesas = Despesa.objects.filter(
        data__year=ano,
        data__month=mes
    ).select_related('categoria')

    # Vendas e itens
    vendas = Venda.objects.filter(data__year=ano, data__month=mes)
    total_liquido = vendas.aggregate(Sum('valor_liquido'))['valor_liquido__sum'] or 0

    itens = ItemVenda.objects.filter(venda__in=vendas).select_related('produto')
    total_ganho_potencial = sum(
        (item.produto.ganho_potencial or 0) * item.quantidade
        for item in itens
    )

    total_despesas = despesas.aggregate(Sum('valor'))['valor__sum'] or 0
    lucro_liquido = total_liquido - total_despesas
    lucro_potencial = total_ganho_potencial - total_despesas

    meses_disponiveis = FinanceiroMes.objects.order_by('-ano', '-mes')

    # Lista de meses para o filtro
    meses = [
        (1, "Janeiro"), (2, "Fevereiro"), (3, "Março"),
        (4, "Abril"), (5, "Maio"), (6, "Junho"),
        (7, "Julho"), (8, "Agosto"), (9, "Setembro"),
        (10, "Outubro"), (11, "Novembro"), (12, "Dezembro")
    ]

    fechamentos = FinanceiroMes.objects.all()

    FinanceiroMes.gerar_ou_atualizar(mes, ano)

    context = {
        'form': form,
        'despesas': despesas,
        'mes': mes,
        'ano': ano,
        'vendas': vendas,
        'itens': itens,
        'total_liquido': round(total_liquido, 2),
        'total_ganho_potencial': round(total_ganho_potencial, 2),
        'total_despesas': round(total_despesas, 2),
        'lucro_liquido': round(lucro_liquido, 2),
        'lucro_potencial': round(lucro_potencial, 2),
        'meses_disponiveis': meses_disponiveis,
        'meses': meses,
        'fechamentos': fechamentos,

    }

    return render(request, 'dash_balanco.html', context)


@csrf_exempt
def excluir_produto(request, produto_id):
    if request.method == 'POST':  # trocado DELETE por POST
        try:
            produto = Produtos.objects.get(id=produto_id)
            produto.delete()
            return JsonResponse({'success': True, 'message': '✅ Produto excluído com sucesso!'})
        except Produtos.DoesNotExist:
            return JsonResponse({'success': False, 'message': '❌ Produto não encontrado.'}, status=404)
    return JsonResponse({'success': False, 'message': 'Método inválido.'}, status=405)


from django.http import JsonResponse
from django.views.decorators.http import require_POST


@require_POST
def adicionar_estoque(request):
    try:
        produto_id = request.POST.get("produto_id")
        qtd = int(request.POST.get("quantidade", 0))
        validade_str = request.POST.get("data_validade")

        produto = Produtos.objects.get(pk=produto_id)
        validade = parse_date(validade_str) if validade_str else None

        estoque = Estoque(
            produtos=produto,
            quantidade=qtd,
            data_validade=validade
        )
        estoque.save()  # já aplica tua lógica de merge e numeração de lotes

        return JsonResponse({
            "success": True,
            "message": "Estoque adicionado com sucesso!"
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "message": str(e)
        })


from django.contrib.auth.models import User


def usuario_criar(request):
    if request.method == "POST":
        username = request.POST.get("username")
        first_name = request.POST.get("first_name")
        last_name = request.POST.get("last_name")
        email = request.POST.get("email")
        password1 = request.POST.get("password1")
        password2 = request.POST.get("password2")

        if password1 != password2:
            return render(request, "criar_user.html", {"erro": "As senhas não coincidem."})

        User.objects.create_user(
            username=username,
            first_name=first_name,
            last_name=last_name,
            email=email,
            password=password1,
            is_staff=False  # importante, não cria staff
        )

        return redirect("usuario_criar")  # ou qualquer página de sucesso
    return render(request, "criar_user.html")


from django.utils.dateparse import parse_date


def dash_stock(request):
    # Pega todos os registros
    saidas = SaidaEstoque.objects.all().order_by('-data_saida')

    # Captura os parâmetros de data do GET
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    if start_date:
        # filtra por data maior ou igual à start_date
        saidas = saidas.filter(data_saida__date__gte=parse_date(start_date))
    if end_date:
        # filtra por data menor ou igual à end_date
        saidas = saidas.filter(data_saida__date__lte=parse_date(end_date))

    context = {
        "saidas": saidas,
        "request": request  # necessário para manter os valores do filtro no template
    }
    return render(request, "dash_stock.html", context)


from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import SaidaEstoque


# View para editar uma saída de estoque
def editar_saida(request, pk):
    saida = get_object_or_404(SaidaEstoque, pk=pk)

    if request.method == "POST":
        nome_produto = request.POST.get("nome_produto")
        codigo_produto = request.POST.get("codigo_produto")
        quantidade = request.POST.get("quantidade")

        if nome_produto:
            saida.nome_produto = nome_produto
        if codigo_produto:
            saida.codigo_produto = codigo_produto
        if quantidade:
            try:
                saida.quantidade = int(quantidade)
            except ValueError:
                messages.error(request, "Quantidade inválida.")
                return redirect("dash_stock")

        saida.save()
        messages.success(request, f"Saída de estoque '{saida.nome_produto}' atualizada com sucesso.")
        return redirect("dash_stock")

    # Caso não seja POST, redireciona
    return redirect("dash_stock")


# View para remover unidades ou todas de uma saída de estoque
@require_POST
def remover_saida(request, pk):
    saida = get_object_or_404(SaidaEstoque, pk=pk)

    # Se clicou no botão "Remover todas"
    if request.POST.get("remover_todas") == "true":
        saida.delete()  # remove o objeto inteiro da lista
    else:
        # Remove apenas a quantidade especificada
        quantidade_remover = int(request.POST.get("quantidade_remover", 0))
        if quantidade_remover >= saida.quantidade:
            saida.delete()  # remove completamente se for igual ou maior
        else:
            saida.quantidade -= quantidade_remover
            saida.save()  # atualiza quantidade

    return redirect("dash_stock")


from django.db.models import Sum

from django.db.models import Sum
from django.utils.dateparse import parse_date
from datetime import datetime, timedelta


def dash_stock_grafico(request):
    # Captura os parâmetros do GET
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    period = request.GET.get('period')

    # Filtra os dados de acordo com os parâmetros
    saidas = SaidaEstoque.objects.all()

    if period:
        today = datetime.today().date()
        if period == 'hoje':
            start_date = end_date = today
        elif period == '7dias':
            start_date = today - timedelta(days=7)
            end_date = today
        elif period == '15dias':
            start_date = today - timedelta(days=15)
            end_date = today
        elif period == '1mes':
            start_date = today - timedelta(days=30)
            end_date = today
        elif period == 'todos':
            start_date = end_date = None

    if start_date:
        saidas = saidas.filter(data_saida__date__gte=start_date)
    if end_date:
        saidas = saidas.filter(data_saida__date__lte=end_date)

    # Agrupa por nome de produto
    data = saidas.values('nome_produto').annotate(total=Sum('quantidade')).order_by('nome_produto')
    labels = [item['nome_produto'] for item in data]
    values = [item['total'] for item in data]

    context = {
        'labels': labels,
        'values': values,
        'request': request
    }
    return render(request, 'dash_stock_grafi.html', context)


from django.shortcuts import render
from django.db.models import Sum
from django.utils import timezone
from datetime import timedelta
from .models import Venda, ItemVenda, SaidaEstoque


def lista_compras(request):
    # Captura o filtro de dias do GET
    dias = int(request.GET.get('dias', 3))  # padrão: 3 dias
    hoje = timezone.now().date()

    # Calcular datas do histórico de 3 semanas
    semanas = 3
    historico_inicio = hoje - timedelta(weeks=semanas)

    # Filtra vendas e saídas dentro das últimas 3 semanas
    vendas_recente = ItemVenda.objects.filter(
        venda__data__date__gte=historico_inicio
    ).values('produto__nome_produto').annotate(
        total_vendido=Sum('quantidade')
    )

    saidas_recente = SaidaEstoque.objects.filter(
        data_saida__date__gte=historico_inicio
    ).values('nome_produto').annotate(
        total_saida=Sum('quantidade')
    )

    # Criar um dicionário para somar todas as quantidades por produto
    produtos_hist = {}

    for item in vendas_recente:
        nome = item['produto__nome_produto']
        produtos_hist[nome] = produtos_hist.get(nome, 0) + item['total_vendido']

    for item in saidas_recente:
        nome = item['nome_produto']
        produtos_hist[nome] = produtos_hist.get(nome, 0) + item['total_saida']

    # Calcula média diária para cada produto e estima quantidade para X dias
    produtos_estimativa = []
    for nome, total in produtos_hist.items():
        media_diaria = total / (semanas * 7)  # média por dia
        quantidade_sugerida = round(media_diaria * dias)
        produtos_estimativa.append({
            'produto': nome,
            'media_diaria': round(media_diaria, 2),
            'dias': dias,
            'quantidade_sugerida': quantidade_sugerida
        })

    # Ordena produtos por quantidade sugerida (opcional)
    produtos_estimativa.sort(key=lambda x: -x['quantidade_sugerida'])

    context = {
        'produtos_estimativa': produtos_estimativa,
        'dias': dias
    }
    return render(request, 'dash_compras.html', context)


@csrf_exempt
def gerar_backup(request):
    if not request.user.is_staff:
        return JsonResponse({"success": False, "error": "Acesso negado."})

    try:
        # Categorias únicas
        categorias = ["Bebidas", "Gelos", "Doses", "Garrafas", "Fracionados", "Energeticos"]
        for nome in categorias:
            CategoriaProduto.objects.get_or_create(nome_categoria=nome)

        # Lista de produtos
        produtos = [
            {"nome": "Coca-Cola 350ml", "codigo": "7894900010015", "categoria": "Bebidas"},
            {"nome": "Gelo coco", "codigo": "1", "categoria": "Gelos"},
            {"nome": "Gelo maracujá", "codigo": "2", "categoria": "Gelos"},
            {"nome": "Dose de gin eternity/Bally", "codigo": "3", "categoria": "Doses"},
            {"nome": "Dose gin gns/Bally", "codigo": "4", "categoria": "Doses"},
            {"nome": "Dose de chanceller/Vibe", "codigo": "5", "categoria": "Doses"},
            {"nome": "Dose chanceller/redbull", "codigo": "6", "categoria": "Doses"},
            {"nome": "Dose White Horse/Vibe", "codigo": "7", "categoria": "Doses"},
            {"nome": "Dose White Horse/redbull", "codigo": "8", "categoria": "Doses"},
            {"nome": "Garrafa White Horse 1L", "codigo": "5000265001335", "categoria": "Garrafas"},
            {"nome": "Garrafa Gns 950ml", "codigo": "7898620850449", "categoria": "Garrafas"},
            {"nome": "Garrafa Eternity Royal 900ml", "codigo": "7898422677817", "categoria": "Garrafas"},
            {"nome": "Garrafa Eternity Tropical 900ml", "codigo": "12", "categoria": "Garrafas"},
            {"nome": "Garrafa Rocks 900ml", "codigo": "13", "categoria": "Garrafas"},
            {"nome": "Garrafa Chanceller 1L", "codigo": "7896072911244", "categoria": "Garrafas"},
            {"nome": "Garrafa Eternity Dry Gin 900ml", "codigo": "7898422671037", "categoria": "Garrafas"},
            {"nome": "Garrafa Rocks Dry gin 1L", "codigo": "7896037916123", "categoria": "Garrafas"},
            {"nome": "Garrafa Seagers Dry Gin 1L", "codigo": "7891121154009", "categoria": "Garrafas"},
            {"nome": "Garrafa Gordons Dry Gin 750ml", "codigo": "5000289020701", "categoria": "Garrafas"},
            {"nome": "Garrafa Jack Daniels 1L", "codigo": "82184090442", "categoria": "Garrafas"},
            {"nome": "Seda Solta", "codigo": "15", "categoria": "Fracionados"},
            {"nome": "Cigarro Solto", "codigo": "16", "categoria": "Fracionados"},
            {"nome": "Garrafa Ballantines Finest 1L", "codigo": "5010106111956", "categoria": "Garrafas"},
            {"nome": "Garrafa Cockland Gold 1L", "codigo": "7896037918097", "categoria": "Garrafas"},
            {"nome": "Garrafa Passaport selection 1L", "codigo": "7891050000101", "categoria": "Garrafas"},
            {"nome": "Garrafa Absolute Vodka 1L", "codigo": "7312040017034", "categoria": "Garrafas"},
            {"nome": "Garrafa Vodka Orloff 1L", "codigo": "7891050001139", "categoria": "Garrafas"},
            {"nome": "Garrafa Askov Vodka 900ml", "codigo": "7896092502460", "categoria": "Garrafas"},
            {"nome": "Redbull Tradicional 250ml", "codigo": "9002490100032", "categoria": "Energeticos"},
        ]

        # Cria os produtos sem preço
        for p in produtos:
            cat, _ = CategoriaProduto.objects.get_or_create(nome_categoria=p["categoria"])
            Produtos.objects.get_or_create(
                codigo=p["codigo"],
                defaults={
                    "nome_produto": p["nome"],
                    "categoria": cat,
                    # Não inclui os campos de preço
                }
            )

        # Categorias de despesas
        despesas = ["Contas", "Investimentos", "Manutenções"]
        for nome in despesas:
            CategoriaDespesas.objects.get_or_create(nome=nome)

        return JsonResponse({"success": True})

    except Exception as e:
        return JsonResponse({"success": False, "error": str(e)})


# core/views.py (ou onde você organiza suas views)
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .models import Despesa


@csrf_exempt
def criar_usuario_padrao(request):
    if request.method == "POST":
        if User.objects.filter(username="admin123").exists():
            return JsonResponse({"success": False, "message": "Usuário joyboy já existe."})

        User.objects.create_superuser(
            username="joyboy",
            email="joyboy@email.com",
            password="joyboy"
        )
        return JsonResponse({"success": True, "message": "Usuário joyboy criado com sucesso!"})
    return JsonResponse({"success": False, "message": "Método não permitido."})
