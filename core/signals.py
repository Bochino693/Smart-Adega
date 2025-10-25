from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import datetime
from core.models import Despesa, Venda, FinanceiroMes

@receiver(post_save, sender=Despesa)
@receiver(post_save, sender=Venda)
def atualizar_fechamento(sender, instance, **kwargs):
    agora = datetime.now()
    FinanceiroMes.gerar_ou_atualizar(mes=agora.month, ano=agora.year)
