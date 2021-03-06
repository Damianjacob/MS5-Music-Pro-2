import os
from django.http import HttpResponse
from django.core.mail import send_mail
from django.template.loader import render_to_string

if "EMAIL_HOST_USER" in os.environ:
    from musicpro.settings import EMAIL_HOST_USER

from .models import Order, OrderLineItem
from products.models import Product
from profiles.models import UserProfile
import json
import time


class StripeWH_Handler:
    """Handle Stripe webhooks"""

    def __init__(self, request):
        self.request = request

    def _send_confirmation_email(self, order):
        """Send confirmation email to the user"""
        cust_email = order.email

        subject = render_to_string(
            'confirmation_emails/confirmation_email_subject.txt',
            {'order': order})

        body = render_to_string(
            'confirmation_emails/confirmation_email_body.txt',
            {'order': order})

        try:
            send_mail(
                subject,
                body,
                EMAIL_HOST_USER,
                [cust_email]
            )
        except Exception as e:
            print(f'There has been an error: {e}')
            print(f'Error type: {type(e)}')
        else:
            print('mail sent successfully')
        
    def _save_user_info_return_profile(self, intent):
        try: 
            shipping_details = intent.shipping
            save_info = intent.metadata.save_info
            profile = None
            username = intent.metadata.username
            if username != 'AnonymousUser':
                profile = UserProfile.objects.get(user__username=username)
                if save_info:
                    profile.default_phone_number = shipping_details.phone
                    profile.default_country = shipping_details.address.country
                    profile.default_postcode = shipping_details.address.postal_code
                    profile.default_town_or_city = shipping_details.address.city
                    profile.default_street_address1=shipping_details.address.line1
                    profile.default_street_address2=shipping_details.address.line2
                    profile.default_county = shipping_details.address.state
                    profile.save()
        except Exception as e:
            print(f'error in save user info function: {e}')
        else:
            print('Everything okay with save user info function')
            
        return profile

    def handle_event(self, event):
        """
        Handle generic Stripe webhook event
        """
        return HttpResponse(
            content=f'Unhandled Webhook received: {event["type"]}',
            status=200
        )

    def handle_payment_intent_succeeded(self, event):
        """
        Handle Stripe payment_intent.succeeded webhook event
        """
        intent = event.data.object
        pid = intent.id
        cart = intent.metadata.cart

        billing_details = intent.charges.data[0].billing_details
        shipping_details = intent.shipping
        grand_total = round(intent.charges.data[0].amount / 100, 2)

        for field, value in shipping_details.address.items():
            if value == '':
                shipping_details.address[field] = None

        # Update profile info if save_info was checked
        try:
            profile = self._save_user_info_return_profile(intent=intent)
        except Exception as e:
            print(f'error while calling save user info function: {e}')
        else:
            print('called user info function successfully')

        order_exists = False
        attempt = 1
        while attempt <= 5:
            try:
                order = Order.objects.get(
                    full_name__iexact=shipping_details.name,
                    email__iexact=billing_details.email,
                    phone_number__iexact=shipping_details.phone,
                    country__iexact=shipping_details.address.country,
                    postcode__iexact=shipping_details.address.postal_code,
                    town_or_city__iexact=shipping_details.address.city,
                    street_address1__iexact=shipping_details.address.line1,
                    street_address2__iexact=shipping_details.address.line2,
                    county__iexact=shipping_details.address.state,
                    grand_total=grand_total,
                    original_cart=cart,
                    stripe_pid=pid
                )

                order_exists = True
                break
            except Order.DoesNotExist:
                attempt += 1
                time.sleep(1)

        if order_exists:
            self._send_confirmation_email(order)

            for item_id, item_quantity in json.loads(cart).items():
                    product = Product.objects.get(id=item_id)

                    # Update availability for each product
                    try:
                        if product.availability > item_quantity:
                            product.availability -= item_quantity
                            product.save()
                        else:
                            raise Exception(f'{product.name} has not enough \
                                    available items in stock.')
                    except Exception as e:
                        print(f'there has been an error: {e}')

            return HttpResponse(
                content=f'Webhook received: {event["type"]} | SUCCESS:\
                        Order is already in database',
                status=200
            )
        else:
            order = None
            try:
                order = Order.objects.create(
                    full_name=shipping_details.name,
                    user_profile=profile,
                    email=billing_details.email,
                    phone_number=shipping_details.phone,
                    country=shipping_details.address.country,
                    postcode=shipping_details.address.postal_code,
                    town_or_city=shipping_details.address.city,
                    street_address1=shipping_details.address.line1,
                    street_address2=shipping_details.address.line2,
                    county=shipping_details.address.state,
                    original_cart=cart,
                    stripe_pid=pid,
                )
                for item_id, item_quantity in json.loads(cart).items():
                    product = Product.objects.get(id=item_id)
                    order_line_item = OrderLineItem(
                        order=order,
                        product=product,
                        quantity=item_quantity,
                    )
                    order_line_item.save()

                    # Update availability for each product
                    try:
                        if product.availability > item_quantity:
                            product.availability -= item_quantity
                            product.save()
                        else:
                            raise Exception(f'{product.name} has not enough \
                                    available items in stock.')
                    except Exception as e:
                        print(f'there has been an error: {e}')
            except Exception as e:
                if order:
                    order.delete()
                return HttpResponse(
                    content=f'Webhook received: {event["type"]} | ERROR: {e}',
                    status=500)
        self._send_confirmation_email(order)
        return HttpResponse(
            content=f'Webhook received: {event["type"]} | SUCCESS\
                Created order in webhook'
        )

    def handle_payment_intent_payment_failed(self, event):
        """
        Handle Stripe payment_intent.payment_failed webhook event
        """
        return HttpResponse(
            content=f'Webhook received: {event["type"]}',
            status=200
        )
