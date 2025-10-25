from django.urls import path
from .views import (vender, estoque, lead_dash, produtos, adicionar_estoque,
                     dash_vendas, dash_vendas_graficos ,excluir_produto, painel, finalizar_venda,
                    profile, estoque_adicao_massa, baixa_geral_estoque, usuario_criar, baixa_unica,
                    cadastrar_produto, get_venda_itens, quitar_venda, dash_stock, editar_saida, remover_saida
                    , dash_stock_grafico,  lista_compras, dashboard_estoque, financeiro_mensal,
                    gerar_backup, criar_usuario_padrao, quitar_pendente, editar_produto)  # ou importe as views necessárias
from django.conf import settings
from django.conf.urls.static import static
from .views import login_view
from django.contrib.auth.views import LogoutView


urlpatterns = [
    path('login/', login_view, name='login'),
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),
    path('', vender, name='vender'),
    path('quitar-pendente/', quitar_pendente, name='quitar_pendente'),  # ✅ nova URL
    path('stock/', estoque, name='estoque'),
    path('stock/adicao-massa/', estoque_adicao_massa, name='estoque_adicao_massa'),
    path('stock/baixar-geral/', baixa_geral_estoque, name='estoque_baixa_geral'),
    path("stock/baixa-unica/", baixa_unica, name="baixa_unica"),
    path('dashboards/', lead_dash, name='lead_dash'),
    path('dash/stock/', dash_stock, name='dash_stock'),
    path('dash/stock/enum/', dashboard_estoque, name='dashboard_estoque'),
    # Editar saída de estoque
    path("dash/stock/editar/<int:pk>/", editar_saida, name="editar_saida"),
    # Remover saída de estoque (total ou parcial)
    path("dash/stock/remover/<int:pk>/", remover_saida, name="remover_saida"),
    path("dash/stock/graficos/", dash_stock_grafico, name="dash_stock_grafico"),
    path("dash/lista-compras/", lista_compras, name="dash_compras"),
    path("dash/balance/", financeiro_mensal, name='balanco'),
    path('criar-usuario-padrao/', criar_usuario_padrao, name='criar_usuario_padrao'),
    path('dash/vendas/', dash_vendas, name='dash_vendas'),
    path('venda/<int:venda_id>/itens/', get_venda_itens, name='get_venda_itens'),
    path('venda/quitar/<int:venda_id>/', quitar_venda, name='quitar_venda'),
    path('dash/vendas/graficos/', dash_vendas_graficos, name='dash_vendas_graficos'),
    path("dash/gerencia-funcionario/criar-usuario/", usuario_criar, name="usuario_criar"),
    path('historico_produtos/', produtos, name='produtos'),
    path('profile/', profile, name='profile'),
    path("produtos/adicionar-estoque/", adicionar_estoque, name="adicionar_estoque"),
    path('produtos/cadastrar-produto/', cadastrar_produto, name='cadastrar_produto'),
    path('produto/<int:pk>/editar/', editar_produto, name='editar_produto'),
    path("painel/", painel, name="painel"),
    path('produtos/excluir/<int:produto_id>/', excluir_produto, name='excluir_produto'),
    path('finalizar-venda/', finalizar_venda, name='finalizar_venda'),
    path('gerar-backup/', gerar_backup, name='gerar_backup'),

]
# Serve arquivos estáticos e media (apenas quando DEBUG=False)
if settings.DEBUG or True:  # sempre para teste, no Render pode colocar True
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
