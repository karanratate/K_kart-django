
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.db import transaction
from django.db.models import F
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.db import close_old_connections
from threading import Thread
from carts.models import CartItem
from .forms import OrderForm
from .models import Order, Payment, OrderProduct
from store.models import Product
import datetime
import json



def send_order_email(user_id, order_id):
    try:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.get(id=user_id)
        order = Order.objects.get(id=order_id)

        message = render_to_string(
            'orders/order_recieved_email.html',
            {
                'user': user,
                'order': order,
            }
        )

        email = EmailMessage(
            'Thank you for your order!',
            message,
            to=[user.email],
        )
        email.content_subtype = 'html'
        email.send(fail_silently=True)

    except Exception as e:
        print("Order email error:", str(e))

    finally:
        close_old_connections()


@login_required
@require_POST
def payments(request):
    try:
        body = json.loads(request.body)

        order_number = body.get('orderID')
        trans_id = body.get('transID')
        payment_method = body.get('payment_method')
        payment_status = body.get('status')

        if not all([order_number, trans_id, payment_method]):
            return JsonResponse(
                {'error': 'Missing payment details'},
                status=400
            )

        # PayPal payment status check
        if payment_status != 'COMPLETED':
            return JsonResponse(
                {'error': 'Payment is not completed'},
                status=400
            )

        with transaction.atomic():

            order = Order.objects.select_for_update().get(
                user=request.user,
                order_number=order_number
            )

            # Handle duplicate confirmation request
            if order.is_ordered:
                if order.payment and order.payment.payment_id == trans_id:
                    return JsonResponse({
                        'order_number': order.order_number,
                        'transID': order.payment.payment_id,
                    })

                return JsonResponse(
                    {'error': 'Order already processed'},
                    status=409
                )

            payment = Payment.objects.create(
                user=request.user,
                payment_id=trans_id,
                payment_method=payment_method,
                amount_paid=order.order_total,
                status=payment_status,
            )

            cart_items = list(
                CartItem.objects.filter(
                    user=request.user
                ).select_related('product').prefetch_related('variations')
            )

            if not cart_items:
                raise ValueError("Cart is empty")

            for item in cart_items:
                orderproduct = OrderProduct.objects.create(
                    order=order,
                    payment=payment,
                    user=request.user,
                    product=item.product,
                    quantity=item.quantity,
                    product_price=item.product.price,
                    ordered=True,
                )

                orderproduct.variations.set(item.variations.all())

                Product.objects.filter(
                    id=item.product_id
                ).update(
                    stock=F('stock') - item.quantity
                )

            order.payment = payment
            order.is_ordered = True
            order.save(update_fields=['payment', 'is_ordered'])

            CartItem.objects.filter(
                user=request.user
            ).delete()

        # Send email without making payment response wait
        Thread(
            target=send_order_email,
            args=(request.user.id, order.id),
            daemon=True
        ).start()

        return JsonResponse({
            'order_number': order.order_number,
            'transID': payment.payment_id,
        })

    except Order.DoesNotExist:
        return JsonResponse(
            {'error': 'Order not found'},
            status=404
        )

    except (json.JSONDecodeError, ValueError) as e:
        return JsonResponse(
            {'error': str(e)},
            status=400
        )

    except Exception as e:
        print("Payment processing error:", str(e))
        return JsonResponse(
            {'error': 'Payment confirmation failed'},
            status=500
        )


def place_order(request, total=0, quantity=0,):
    current_user = request.user

    # If the cart count is less than or equal to 0, then redirect back to shop
    cart_items = CartItem.objects.filter(user=current_user)
    cart_count = cart_items.count()
    if cart_count <= 0:
        return redirect('store')
    
    grand_total = 0
    tax = 0
    for cart_item in cart_items:
        total += (cart_item.product.price * cart_item.quantity)
        quantity += cart_item.quantity
    tax = (2 * total)/100
    grand_total = total + tax

    
    if request.method == 'POST':
        form = OrderForm(request.POST)
        if form.is_valid():
            # Store all the billing information inside Order table
            data = Order()
            data.user = current_user
            data.first_name = form.cleaned_data['first_name']
            data.last_name = form.cleaned_data['last_name']
            data.phone = form.cleaned_data['phone']
            data.email = form.cleaned_data['email']
            data.address_line_1 = form.cleaned_data['address_line_1']
            data.address_line_2 = form.cleaned_data['address_line_2']
            data.country = form.cleaned_data['country']
            data.state = form.cleaned_data['state']
            data.city = form.cleaned_data['city']
            data.order_note = form.cleaned_data['order_note']
            data.order_total = grand_total
            data.tax = tax
            data.ip = request.META.get('REMOTE_ADDR')
            data.save()
            # Generate order number
            yr = int(datetime.date.today().strftime('%Y'))
            dt = int(datetime.date.today().strftime('%d'))
            mt = int(datetime.date.today().strftime('%m'))
            d = datetime.date(yr,mt,dt)
            current_date = d.strftime("%Y%m%d") #20210305
            order_number = current_date + str(data.id)
            data.order_number = order_number
            data.save()
            
            order = Order.objects.get(user=current_user, is_ordered=False, order_number=order_number)
            context = {
                'order': order,
                'cart_items': cart_items,
                'total': total,
                'tax': tax,
                'grand_total': grand_total,
            }
            return render(request, 'orders/payments.html', context)
    else:
        return redirect('checkout')
    
def order_complete(request):
    order_number = request.GET.get('order_number')
    transID = request.GET.get('payment_id')

    try:
        order = Order.objects.get(order_number=order_number, is_ordered=True)
        ordered_products = OrderProduct.objects.filter(order_id=order.id)

        subtotal = 0
        for i in ordered_products:
            subtotal += i.product_price * i.quantity

        payment = Payment.objects.get(payment_id=transID)

        context = {
            'order': order,
            'ordered_products': ordered_products,
            'order_number': order.order_number,
            'transID': payment.payment_id,
            'payment': payment,
            'subtotal': subtotal,
        }
        return render(request, 'orders/order_complete.html', context)
    except (Payment.DoesNotExist, Order.DoesNotExist):
        return redirect('home')
    