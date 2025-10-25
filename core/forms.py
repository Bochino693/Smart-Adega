from django import forms
from django.utils import timezone
from .models import Produtos, Estoque


class ProdutoForm(forms.ModelForm):
    class Meta:
        model = Produtos
        fields = ['codigo', 'nome_produto', 'preco_venda', 'preco_fornecedor', 'categoria', 'imagem']
        widgets = {
            'codigo': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Código do produto'}),
            'nome_produto': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nome do produto'}),
            'preco_venda': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Preço de venda'}),
            'preco_fornecedor': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'placeholder': 'Preço fornecedor'}),
            'categoria': forms.Select(attrs={'class': 'form-control'}),
            'imagem': forms.ClearableFileInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Campos opcionais
        self.fields['preco_venda'].required = False
        self.fields['preco_fornecedor'].required = False
        self.fields['categoria'].required = False
        self.fields['imagem'].required = False



class EstoqueForm(forms.ModelForm):
    class Meta:
        model = Estoque
        fields = ['produtos', 'quantidade', 'data_validade']
        widgets = {
            'produtos': forms.Select(attrs={'class': 'form-control'}),
            'quantidade': forms.NumberInput(attrs={'class': 'form-control', 'min': '1'}),
            'data_validade': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }


from .models import Despesa, CategoriaDespesas

class DespesaForm(forms.ModelForm):
    data = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
        required=True,
        label="Data da Despesa"
    )

    class Meta:
        model = Despesa
        fields = ['categoria', 'descricao', 'valor', 'data']  # ✅ Inclui o campo data
        widgets = {
            'categoria': forms.Select(attrs={'class': 'form-select'}),
            'descricao': forms.TextInput(attrs={'class': 'form-control'}),
            'valor': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        }
