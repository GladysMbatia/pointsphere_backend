from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path("register/", views.register),
    path("login/", views.login),

    # Customer (view-only)
    path("customer/profile/", views.customer_profile),
    path("customer/report/", views.customer_report),

    # Partner (view-only)
    path("partner/dashboard/", views.partner_dashboard),
    path("partner/report/", views.partner_report),
    path("partner/invoice/", views.partner_invoice),        
    path("partner/analytics/", views.partner_analytics), 

    # POS (called by partner POS systems)
    path("pos/earn/", views.pos_earn),
    path("pos/redeem/", views.pos_redeem),
    path("pos/customer-lookup/", views.pos_customer_lookup),

    # Admin
    path("admin/dashboard/", views.admin_dashboard),
    path("admin/toggle-partner/", views.admin_toggle_partner),
    path("admin/float/deposit/", views.admin_float_deposit),
    path("admin/conversion-rates/", views.admin_conversion_rates),
    path("admin/conversion-rates/set/", views.admin_set_conversion_rate),
    path("admin/cashier/add/", views.admin_add_cashier),
    path("admin/audit-log/", views.admin_audit_log),
    path("admin/report/", views.admin_report),
    path("admin/partner-invoice/", views.partner_invoice),
]
