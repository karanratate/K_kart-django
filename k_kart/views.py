
from django.shortcuts import render
from django.http import HttpResponse
import logging

logger = logging.getLogger(__name__)

def home(request):
    try:
        from store.models import Product

        products = Product.objects.filter(is_available=True)

        return render(request, 'home.html', {
            'products': products,
        })

    except Exception:
        logger.exception("HOMEPAGE ERROR")
        return HttpResponse("Homepage error. Please check Render logs.", status=500)
