import json
from decimal import Decimal
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import User, PartnerProfile, ConversionRate, FloatTransaction, Transaction, AuditLog


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def get_user_from_token(request, expected_role=None):
    token = request.headers.get("Authorization", "").strip()
    if not token:
        token = request.GET.get("token", "").strip()
    if not token:
        return None, JsonResponse({"error": "No token provided"}, status=401)
    try:
        user = User.objects.get(token=token)
    except User.DoesNotExist:
        return None, JsonResponse({"error": "Invalid token"}, status=401)
    if expected_role:
        if isinstance(expected_role, list):
            if user.role not in expected_role:
                return None, JsonResponse({"error": "Unauthorized role"}, status=403)
        elif user.role != expected_role:
            return None, JsonResponse({"error": "Unauthorized role"}, status=403)
    return user, None


def log_action(request, user, action, target="", detail=""):
    ip = request.META.get("REMOTE_ADDR")
    AuditLog.objects.create(user=user, action=action, target=target, detail=detail, ip_address=ip)


def get_client_ip(request):
    return request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")).split(",")[0].strip()


# ─────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────
@csrf_exempt
@require_http_methods(["POST"])
def register(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name  = data.get("name", "").strip()
    phone = data.get("phone", "").strip()
    pin   = data.get("pin", "").strip()
    role  = data.get("role", "customer").strip()

    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)
    if len(phone) != 9 or not phone.isdigit():
        return JsonResponse({"error": "Phone must be 9 digits"}, status=400)
    if len(pin) != 4 or not pin.isdigit():
        return JsonResponse({"error": "PIN must be 4 digits"}, status=400)
    if role not in ("customer", "partner", "admin"):
        return JsonResponse({"error": "Invalid role"}, status=400)
    if User.objects.filter(phone=phone, role=role).exists():
        return JsonResponse({"error": "Account already exists for this phone and role"}, status=409)

    user = User(name=name, phone=phone, role=role)
    user.set_pin(pin)
    user.save()

    if role == "partner":
        PartnerProfile.objects.create(user=user, business_name=name)
        ConversionRate.objects.create(partner=user, points_per_ksh=Decimal("0.1"), updated_by=None)

    log_action(request, user, "register", target=f"{role}:{phone}")
    return JsonResponse({"message": "Account created successfully", "role": role}, status=201)


@csrf_exempt
@require_http_methods(["POST"])
def login(request):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    phone = data.get("phone", "").strip()
    pin   = data.get("pin", "").strip()
    role  = data.get("role", "customer").strip()

    if len(phone) != 9 or not phone.isdigit():
        return JsonResponse({"error": "Invalid phone number"}, status=400)
    if len(pin) != 4 or not pin.isdigit():
        return JsonResponse({"error": "Invalid PIN"}, status=400)

    try:
        user = User.objects.get(phone=phone, role=role)
    except User.DoesNotExist:
        return JsonResponse({"error": "No account found for this phone and role"}, status=404)

    if not user.check_pin(pin):
        log_action(request, user, "login_failed", target=f"{role}:{phone}")
        return JsonResponse({"error": "Incorrect PIN"}, status=401)

    token = user.generate_token()
    user.save(update_fields=["token"])
    log_action(request, user, "login", target=f"{role}:{phone}")

    return JsonResponse({"token": token, "role": user.role, "name": user.name})


# ─────────────────────────────────────────
# CUSTOMER (view-only)
# ─────────────────────────────────────────
@csrf_exempt
@require_http_methods(["GET"])
def customer_profile(request):
    user, err = get_user_from_token(request, "customer")
    if err:
        return err

    transactions = Transaction.objects.filter(customer=user).order_by("-created_at")[:20]
    tx_list = [
        {
            "type": t.transaction_type,
            "points": t.points,
            "amount_ksh": float(t.amount_ksh),
            "monetary_value": float(t.monetary_value),
            "partner": t.partner.name if t.partner else "PointSphere",
            "note": t.note,
            "date": t.created_at.strftime("%d %b %Y %H:%M"),
        }
        for t in transactions
    ]

    earned   = sum(t.points for t in Transaction.objects.filter(customer=user, transaction_type="earn"))
    redeemed = sum(t.points for t in Transaction.objects.filter(customer=user, transaction_type="redeem"))

    return JsonResponse({
        "name": user.name,
        "phone": user.phone,
        "points": user.points,
        "total_earned": earned,
        "total_redeemed": redeemed,
        "transactions": tx_list,
    })


# ─────────────────────────────────────────
# PARTNER (view-only)
# ─────────────────────────────────────────
@csrf_exempt
@require_http_methods(["GET"])
def partner_dashboard(request):
    user, err = get_user_from_token(request, "partner")
    if err:
        return err

    profile, _ = PartnerProfile.objects.get_or_create(user=user, defaults={"business_name": user.name})

    transactions = Transaction.objects.filter(partner=user).order_by("-created_at")[:20]
    tx_list = [
        {
            "customer": t.customer.name,
            "type": t.transaction_type,
            "points": t.points,
            "amount_ksh": float(t.amount_ksh),
            "note": t.note,
            "pos_reference": t.pos_reference,
            "date": t.created_at.strftime("%d %b %Y %H:%M"),
        }
        for t in transactions
    ]

    points_issued   = sum(t.points for t in Transaction.objects.filter(partner=user, transaction_type="earn"))
    points_redeemed = sum(t.points for t in Transaction.objects.filter(partner=user, transaction_type="redeem"))

    # Conversion rate
    rate = getattr(user, 'conversion_rate', None)

    # Float transactions
    float_txs = FloatTransaction.objects.filter(partner=user).order_by("-created_at")[:10]
    float_history = [
        {
            "type": f.transaction_type,
            "amount": float(f.amount),
            "balance_after": float(f.balance_after),
            "note": f.note,
            "reference": f.reference,
            "date": f.created_at.strftime("%d %b %Y %H:%M"),
        }
        for f in float_txs
    ]

    return JsonResponse({
        "name": user.name,
        "business_name": profile.business_name,
        "float_balance": float(profile.float_balance),
        "min_float_threshold": float(profile.min_float_threshold),
        "float_low": profile.is_float_low(),
        "points_issued": points_issued,
        "points_redeemed": points_redeemed,
        "is_active": profile.is_active,
        "conversion_rate": float(rate.points_per_ksh) if rate else 0.1,
        "transactions": tx_list,
        "float_history": float_history,
    })


# ─────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────
@csrf_exempt
@require_http_methods(["GET"])
def admin_dashboard(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err

    customers    = User.objects.filter(role="customer")
    partners     = User.objects.filter(role="partner")
    total_points = sum(c.points for c in customers)
    total_float  = sum(
        float(p.partner_profile.float_balance)
        for p in partners if hasattr(p, "partner_profile")
    )

    partner_list = []
    for p in partners:
        profile = getattr(p, "partner_profile", None)
        rate    = getattr(p, "conversion_rate", None)
        partner_list.append({
            "id": p.id,
            "name": p.name,
            "phone": p.phone,
            "business_name": profile.business_name if profile else p.name,
            "float_balance": float(profile.float_balance) if profile else 0,
            "min_float_threshold": float(profile.min_float_threshold) if profile else 1000,
            "float_low": profile.is_float_low() if profile else False,
            "is_active": profile.is_active if profile else True,
            "conversion_rate": float(rate.points_per_ksh) if rate else 0.1,
        })

    return JsonResponse({
        "total_customers": customers.count(),
        "total_partners": partners.count(),
        "total_points_in_circulation": total_points,
        "total_float": total_float,
        "partners": partner_list,
    })


@csrf_exempt
@require_http_methods(["POST"])
def admin_toggle_partner(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    try:
        profile = PartnerProfile.objects.get(user__id=data.get("partner_id"))
    except PartnerProfile.DoesNotExist:
        return JsonResponse({"error": "Partner not found"}, status=404)

    profile.is_active = not profile.is_active
    profile.save(update_fields=["is_active"])
    log_action(request, user, "toggle_partner", target=f"partner:{profile.user.phone}",
               detail=f"is_active={profile.is_active}")

    return JsonResponse({"message": f"Partner {'activated' if profile.is_active else 'deactivated'}", "is_active": profile.is_active})


@csrf_exempt
@require_http_methods(["POST"])
def admin_float_deposit(request):
    """Admin deposits float for a partner."""
    user, err = get_user_from_token(request, "admin")
    if err:
        return err
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    partner_id = data.get("partner_id")
    amount     = Decimal(str(data.get("amount", 0)))
    reference  = data.get("reference", "").strip()
    note       = data.get("note", "Float deposit").strip()

    if amount <= 0:
        return JsonResponse({"error": "Amount must be positive"}, status=400)

    try:
        profile = PartnerProfile.objects.get(user__id=partner_id)
    except PartnerProfile.DoesNotExist:
        return JsonResponse({"error": "Partner not found"}, status=404)

    profile.float_balance += amount
    profile.save(update_fields=["float_balance"])

    FloatTransaction.objects.create(
        partner=profile.user, transaction_type="deposit",
        amount=amount, balance_after=profile.float_balance,
        note=note, reference=reference, created_by=user,
    )

    log_action(request, user, "float_deposit",
               target=f"partner:{profile.user.phone}",
               detail=f"amount={amount} ref={reference}")

    low_warning = profile.is_float_low()
    return JsonResponse({
        "message": f"Deposited KSh {amount} to {profile.business_name}",
        "new_balance": float(profile.float_balance),
        "float_low": low_warning,
    })


@csrf_exempt
@require_http_methods(["POST"])
def admin_set_conversion_rate(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    partner_id     = data.get("partner_id")
    points_per_ksh = Decimal(str(data.get("points_per_ksh", 0.1)))
    min_spend      = Decimal(str(data.get("min_spend_ksh", 0)))

    if points_per_ksh <= 0:
        return JsonResponse({"error": "Rate must be positive"}, status=400)

    try:
        partner = User.objects.get(id=partner_id, role="partner")
    except User.DoesNotExist:
        return JsonResponse({"error": "Partner not found"}, status=404)

    rate, _ = ConversionRate.objects.update_or_create(
        partner=partner,
        defaults={"points_per_ksh": points_per_ksh, "min_spend_ksh": min_spend, "updated_by": user},
    )

    log_action(request, user, "set_conversion_rate",
               target=f"partner:{partner.phone}",
               detail=f"rate={points_per_ksh} min_spend={min_spend}")

    return JsonResponse({
        "message": f"Rate set for {partner.name}",
        "points_per_ksh": float(rate.points_per_ksh),
        "min_spend_ksh": float(rate.min_spend_ksh),
    })


@csrf_exempt
@require_http_methods(["GET"])
def admin_conversion_rates(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err

    rates = ConversionRate.objects.select_related("partner").all()
    return JsonResponse({
        "rates": [
            {
                "partner_id": r.partner.id,
                "partner_name": r.partner.name,
                "points_per_ksh": float(r.points_per_ksh),
                "min_spend_ksh": float(r.min_spend_ksh),
                "updated_at": r.updated_at.strftime("%d %b %Y %H:%M"),
            }
            for r in rates
        ]
    })


@csrf_exempt
@require_http_methods(["GET"])
def admin_audit_log(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err

    logs = AuditLog.objects.select_related("user").order_by("-created_at")[:100]
    return JsonResponse({
        "logs": [
            {
                "user": l.user.name if l.user else "System",
                "role": l.user.role if l.user else "—",
                "action": l.action,
                "target": l.target,
                "detail": l.detail,
                "ip": l.ip_address,
                "date": l.created_at.strftime("%d %b %Y %H:%M:%S"),
            }
            for l in logs
        ]
    })


# ─────────────────────────────────────────
# POS API  (called by partner POS systems)
# ─────────────────────────────────────────
@csrf_exempt
@require_http_methods(["POST"])
def pos_earn(request):
    """
    POS system awards points to a customer after a purchase.
    Auth: partner token in Authorization header.
    Body: { phone, amount_ksh, pos_reference }
    """
    actor, err = get_user_from_token(request, ["partner"])
    if err:
        return err

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    phone         = data.get("phone", "").strip()
    amount_ksh    = float(data.get("amount_ksh", 0))
    pos_reference = data.get("pos_reference", "").strip()
    note          = data.get("note", "Purchase at partner").strip()

    if len(phone) != 9 or not phone.isdigit():
        return JsonResponse({"error": "Invalid phone number"}, status=400)
    if amount_ksh <= 0:
        return JsonResponse({"error": "Amount must be positive"}, status=400)

    try:
        customer = User.objects.get(phone=phone, role="customer")
    except User.DoesNotExist:
        return JsonResponse({"error": "Customer not found"}, status=404)

    # Get conversion rate
    try:
        rate = ConversionRate.objects.get(partner=partner)
    except ConversionRate.DoesNotExist:
        rate = None

    points = rate.calculate_points(amount_ksh) if rate else int(amount_ksh * 0.1)

    if points <= 0:
        return JsonResponse({"error": f"Spend of KSh {amount_ksh} does not meet minimum for earning points"}, status=400)

    # Reserve float (deduct from partner)
    profile = getattr(partner, "partner_profile", None)
    monetary_value = Decimal(str(points))  # 1pt = KSh 1 (configurable later)

    if profile:
        profile.float_balance -= monetary_value
        profile.save(update_fields=["float_balance"])
        FloatTransaction.objects.create(
            partner=partner, transaction_type="reserve",
            amount=monetary_value, balance_after=profile.float_balance,
            note=f"Points reserved for {customer.name}",
            reference=pos_reference, created_by=actor,
        )

    # Award points
    customer.points += points
    customer.save(update_fields=["points"])

    tx = Transaction.objects.create(
        customer=customer, partner=partner, 
        transaction_type="earn", points=points,
        amount_ksh=Decimal(str(amount_ksh)),
        monetary_value=monetary_value,
        note=note, pos_reference=pos_reference,
    )

    log_action(request, actor, "pos_earn",
               target=f"customer:{phone}",
               detail=f"points={points} amount={amount_ksh} ref={pos_reference}")

    # Low float warning
    low_warning = profile.is_float_low() if profile else False

    return JsonResponse({
        "success": True,
        "customer_name": customer.name,
        "points_awarded": points,
        "new_balance": customer.points,
        "monetary_value": float(monetary_value),
        "transaction_id": tx.id,
        "float_low_warning": low_warning,
    })


@csrf_exempt
@require_http_methods(["POST"])
def pos_redeem(request):
    """
    POS system redeems points for a customer.
    Auth: partner token.
    Body: { phone, points, pos_reference }
    """
    actor, err = get_user_from_token(request, ["partner"])
    if err:
        return err

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    phone         = data.get("phone", "").strip()
    points        = int(data.get("points", 0))
    pos_reference = data.get("pos_reference", "").strip()
    note          = data.get("note", "Redemption at partner").strip()

    if len(phone) != 9 or not phone.isdigit():
        return JsonResponse({"error": "Invalid phone number"}, status=400)
    if points <= 0:
        return JsonResponse({"error": "Points must be positive"}, status=400)

    try:
        customer = User.objects.get(phone=phone, role="customer")
    except User.DoesNotExist:
        return JsonResponse({"error": "Customer not found"}, status=404)

    if customer.points < points:
        return JsonResponse({"error": f"Insufficient points. Balance: {customer.points}"}, status=400)

    monetary_value = Decimal(str(points))  # 1pt = KSh 1
    profile = getattr(partner, "partner_profile", None)
    liability_created = False

    if profile:
        if profile.float_balance >= monetary_value:
            # Normal deduction
            profile.float_balance -= monetary_value
            profile.save(update_fields=["float_balance"])
            FloatTransaction.objects.create(
                partner=partner, transaction_type="deduction",
                amount=monetary_value, balance_after=profile.float_balance,
                note=f"Redemption by {customer.name}",
                reference=pos_reference, created_by=actor,
            )
        else:
            # Float insufficient — record liability
            shortfall = monetary_value - profile.float_balance
            profile.float_balance = Decimal("0")
            profile.save(update_fields=["float_balance"])
            FloatTransaction.objects.create(
                partner=partner, transaction_type="liability",
                amount=shortfall, balance_after=Decimal("0"),
                note=f"Shortfall covered by central pool for {customer.name}",
                reference=pos_reference, created_by=actor,
            )
            liability_created = True

    # Deduct points
    customer.points -= points
    customer.save(update_fields=["points"])

    tx = Transaction.objects.create(
        customer=customer, partner=partner, 
        transaction_type="redeem", points=points,
        monetary_value=monetary_value,
        note=note, pos_reference=pos_reference,
    )

    log_action(request, actor, "pos_redeem",
               target=f"customer:{phone}",
               detail=f"points={points} ref={pos_reference} liability={liability_created}")

    low_warning = profile.is_float_low() if profile else False

    return JsonResponse({
        "success": True,
        "customer_name": customer.name,
        "points_redeemed": points,
        "new_balance": customer.points,
        "monetary_value": float(monetary_value),
        "transaction_id": tx.id,
        "liability_created": liability_created,
        "float_low_warning": low_warning,
    })


@csrf_exempt
@require_http_methods(["GET"])
def pos_customer_lookup(request):
    """POS looks up customer by phone to verify before transaction."""
    actor, err = get_user_from_token(request, ["partner"])
    if err:
        return err

    phone = request.GET.get("phone", "").strip()
    if len(phone) != 9 or not phone.isdigit():
        return JsonResponse({"error": "Invalid phone"}, status=400)

    try:
        customer = User.objects.get(phone=phone, role="customer")
    except User.DoesNotExist:
        return JsonResponse({"error": "Customer not found"}, status=404)

    return JsonResponse({
        "name": customer.name,
        "phone": customer.phone,
        "points": customer.points,
    })


# ─────────────────────────────────────────
# REPORTS
# ─────────────────────────────────────────
from django.http import HttpResponse
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER
from io import BytesIO
from datetime import datetime


def build_pdf(title, subtitle, sections):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle("title", fontSize=22, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#c4622d"), alignment=TA_CENTER, spaceAfter=4)
    style_sub   = ParagraphStyle("sub",   fontSize=11, fontName="Helvetica",
                                  textColor=colors.HexColor("#888888"), alignment=TA_CENTER, spaceAfter=2)
    style_date  = ParagraphStyle("date",  fontSize=9,  fontName="Helvetica",
                                  textColor=colors.HexColor("#aaaaaa"), alignment=TA_CENTER, spaceAfter=14)
    style_head  = ParagraphStyle("head",  fontSize=13, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#c4622d"), spaceBefore=12, spaceAfter=6)
    style_text  = ParagraphStyle("text",  fontSize=10, fontName="Helvetica",
                                  textColor=colors.HexColor("#333333"), spaceAfter=6)

    story = []
    story.append(Paragraph("PointSphere", style_title))
    story.append(Paragraph(title, style_sub))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%d %b %Y at %H:%M')}", style_date))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c4622d")))
    story.append(Spacer(1, 8*mm))
    if subtitle:
        story.append(Paragraph(subtitle, style_text))
        story.append(Spacer(1, 4*mm))

    for section in sections:
        story.append(Paragraph(section["heading"], style_head))
        if "text" in section:
            story.append(Paragraph(section["text"], style_text))
        if "table" in section:
            rows = section["table"]
            col_count = len(rows[0]) if rows else 1
            col_width = (A4[0] - 40*mm) / col_count
            t = Table(rows, colWidths=[col_width]*col_count, repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#c4622d")),
                ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
                ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
                ("FONTSIZE",      (0,0), (-1,0), 10),
                ("ALIGN",         (0,0), (-1,0), "CENTER"),
                ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
                ("FONTSIZE",      (0,1), (-1,-1), 9),
                ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor("#fff8f4"), colors.white]),
                ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#dddddd")),
                ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
                ("TOPPADDING",    (0,0), (-1,-1), 5),
                ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ]))
            story.append(t)
        story.append(Spacer(1, 4*mm))

    doc.build(story)
    buffer.seek(0)
    return buffer


@csrf_exempt
@require_http_methods(["GET"])
def customer_report(request):
    user, err = get_user_from_token(request, "customer")
    if err:
        return err

    transactions = Transaction.objects.filter(customer=user).order_by("-created_at")
    earned   = sum(t.points for t in transactions if t.transaction_type == "earn")
    redeemed = sum(t.points for t in transactions if t.transaction_type == "redeem")

    summary_rows = [
        ["Metric", "Value"],
        ["Current Points Balance", str(user.points)],
        ["Total Points Earned",    str(earned)],
        ["Total Points Redeemed",  str(redeemed)],
        ["Total Transactions",     str(len(transactions))],
    ]

    tx_rows = [["Date", "Partner", "Type", "Points", "KSh Spent", "Note"]]
    for t in transactions:
        tx_rows.append([
            t.created_at.strftime("%d %b %Y %H:%M"),
            t.partner.name if t.partner else "—",
            t.transaction_type.capitalize(),
            ("+" if t.transaction_type == "earn" else "-") + str(t.points),
            f"KSh {float(t.amount_ksh):,.0f}" if t.amount_ksh else "—",
            t.note or "—",
        ])
    if len(tx_rows) == 1:
        tx_rows.append(["—","—","—","—","—","No transactions yet"])

    buffer = build_pdf(
        title=f"Customer Report — {user.name}",
        subtitle=f"Phone: {user.phone}  |  Account since: {user.created_at.strftime('%d %b %Y')}",
        sections=[
            {"heading": "Points Summary",     "table": summary_rows},
            {"heading": "Transaction History","table": tx_rows},
        ],
    )
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ps_customer_{user.phone}.pdf"'
    return response


@csrf_exempt
@require_http_methods(["GET"])
def partner_report(request):
    user, err = get_user_from_token(request, "partner")
    if err:
        return err

    profile, _ = PartnerProfile.objects.get_or_create(user=user, defaults={"business_name": user.name})
    transactions = Transaction.objects.filter(partner=user).order_by("-created_at")
    float_txs    = FloatTransaction.objects.filter(partner=user).order_by("-created_at")
    earned   = sum(t.points for t in transactions if t.transaction_type == "earn")
    redeemed = sum(t.points for t in transactions if t.transaction_type == "redeem")
    rate     = getattr(user, "conversion_rate", None)

    summary_rows = [
        ["Metric", "Value"],
        ["Business Name",        profile.business_name or user.name],
        ["Float Balance",        f"KSh {float(profile.float_balance):,.2f}"],
        ["Min Float Threshold",  f"KSh {float(profile.min_float_threshold):,.2f}"],
        ["Float Status",         "LOW ⚠" if profile.is_float_low() else "Healthy"],
        ["Points Issued",        str(earned)],
        ["Points Redeemed",      str(redeemed)],
        ["Conversion Rate",      f"{float(rate.points_per_ksh)} pt/KSh" if rate else "0.1 pt/KSh"],
        ["Status",               "Active" if profile.is_active else "Inactive"],
    ]

    tx_rows = [["Date", "Customer", "Type", "Points", "KSh", "POS Ref"]]
    for t in transactions:
        tx_rows.append([
            t.created_at.strftime("%d %b %Y %H:%M"),
            t.customer.name,
            t.transaction_type.capitalize(),
            ("+" if t.transaction_type == "earn" else "-") + str(t.points),
            f"KSh {float(t.amount_ksh):,.0f}" if t.amount_ksh else "—",
            t.pos_reference or "—",
        ])
    if len(tx_rows) == 1:
        tx_rows.append(["—","—","—","—","—","No transactions yet"])

    float_rows = [["Date", "Type", "Amount (KSh)", "Balance After", "Reference"]]
    for f in float_txs:
        float_rows.append([
            f.created_at.strftime("%d %b %Y %H:%M"),
            f.transaction_type.capitalize(),
            f"KSh {float(f.amount):,.2f}",
            f"KSh {float(f.balance_after):,.2f}",
            f.reference or "—",
        ])
    if len(float_rows) == 1:
        float_rows.append(["—","—","—","—","No float history"])

    buffer = build_pdf(
        title=f"Partner Report — {profile.business_name or user.name}",
        subtitle=f"Phone: {user.phone}  |  Partner since: {user.created_at.strftime('%d %b %Y')}",
        sections=[
            {"heading": "Performance Summary", "table": summary_rows},
            {"heading": "Transaction History", "table": tx_rows},
            {"heading": "Float History",       "table": float_rows},
        ],
    )
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ps_partner_{user.phone}.pdf"'
    return response


@csrf_exempt
@require_http_methods(["GET"])
def admin_report(request):
    user, err = get_user_from_token(request, "admin")
    if err:
        return err

    customers    = User.objects.filter(role="customer")
    partners     = User.objects.filter(role="partner")
    transactions = Transaction.objects.order_by("-created_at")
    total_points = sum(c.points for c in customers)
    total_float  = sum(float(p.partner_profile.float_balance) for p in partners if hasattr(p, "partner_profile"))

    system_rows = [
        ["Metric", "Value"],
        ["Total Customers",           str(customers.count())],
        ["Total Partners",            str(partners.count())],
        ["Points in Circulation",     str(total_points)],
        ["Total Float (KSh)",         f"{total_float:,.2f}"],
        ["Total Transactions",        str(transactions.count())],
        ["Report Generated",          datetime.now().strftime("%d %b %Y %H:%M")],
    ]

    customer_rows = [["Name", "Phone", "Points", "Joined"]]
    for c in customers:
        customer_rows.append([c.name, c.phone, str(c.points), c.created_at.strftime("%d %b %Y")])
    if len(customer_rows) == 1:
        customer_rows.append(["—","—","—","No customers yet"])

    partner_rows = [["Name", "Business", "Float (KSh)", "Rate (pt/KSh)", "Status"]]
    for p in partners:
        profile = getattr(p, "partner_profile", None)
        rate    = getattr(p, "conversion_rate", None)
        partner_rows.append([
            p.name,
            profile.business_name if profile else "—",
            f"{float(profile.float_balance):,.2f}" if profile else "0.00",
            f"{float(rate.points_per_ksh)}" if rate else "0.1",
            "Active" if (profile and profile.is_active) else "Inactive",
        ])
    if len(partner_rows) == 1:
        partner_rows.append(["—","—","—","—","No partners yet"])

    tx_rows = [["Date", "Customer", "Partner", "Type", "Points", "KSh", "POS Ref"]]
    for t in transactions[:50]:
        tx_rows.append([
            t.created_at.strftime("%d %b %Y %H:%M"),
            t.customer.name,
            t.partner.name if t.partner else "—",
            t.transaction_type.capitalize(),
            ("+" if t.transaction_type == "earn" else "-") + str(t.points),
            f"KSh {float(t.amount_ksh):,.0f}" if t.amount_ksh else "—",
            t.pos_reference or "—",
        ])
    if len(tx_rows) == 1:
        tx_rows.append(["—","—","—","—","—","—","No transactions yet"])

    # Audit log
    logs = AuditLog.objects.order_by("-created_at")[:30]
    audit_rows = [["Date", "User", "Role", "Action", "Target"]]
    for l in logs:
        audit_rows.append([
            l.created_at.strftime("%d %b %Y %H:%M"),
            l.user.name if l.user else "System",
            l.user.role if l.user else "—",
            l.action,
            l.target,
        ])
    if len(audit_rows) == 1:
        audit_rows.append(["—","—","—","—","No audit entries"])

    buffer = build_pdf(
        title="Admin System Report",
        subtitle="Full overview of all customers, partners, float, transactions and audit log.",
        sections=[
            {"heading": "System Overview",     "table": system_rows},
            {"heading": "Customer Summary",    "table": customer_rows},
            {"heading": "Partner Summary",     "table": partner_rows},
            {"heading": "Recent Transactions", "table": tx_rows},
            {"heading": "Audit Log",           "table": audit_rows},
        ],
    )
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="ps_admin_report.pdf"'
    return response

# ── INVOICE GENERATION ───────────────────
@csrf_exempt
@require_http_methods(["GET"])
def partner_invoice(request):
    """Generate a PDF invoice for a partner's outstanding liability."""
    user, err = get_user_from_token(request, ["partner", "admin"])
    if err:
        return err

    # Admin can view any partner's invoice via ?partner_id=X
    if user.role == "admin":
        partner_id = request.GET.get("partner_id")
        if not partner_id:
            return JsonResponse({"error": "partner_id required"}, status=400)
        try:
            partner = User.objects.get(id=partner_id, role="partner")
        except User.DoesNotExist:
            return JsonResponse({"error": "Partner not found"}, status=404)
    else:
        partner = user

    profile = getattr(partner, "partner_profile", None)
    if not profile:
        return JsonResponse({"error": "Partner profile not found"}, status=404)

    # Get all unpaid liabilities
    liabilities = FloatTransaction.objects.filter(
        partner=partner, transaction_type="liability"
    ).order_by("-created_at")

    settlements = FloatTransaction.objects.filter(
        partner=partner, transaction_type="settlement"
    )

    total_liability  = sum(l.amount for l in liabilities)
    total_settled    = sum(s.amount for s in settlements)
    amount_due       = max(total_liability - total_settled, Decimal("0"))

    invoice_number   = f"INV-{partner.id:04d}-{datetime.now().strftime('%Y%m%d')}"

    # Build PDF
    buffer   = BytesIO()
    doc      = SimpleDocTemplate(buffer, pagesize=A4,
                                  leftMargin=20*mm, rightMargin=20*mm,
                                  topMargin=20*mm, bottomMargin=20*mm)
    styles   = getSampleStyleSheet()
    story    = []

    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    s_title  = ParagraphStyle("t", fontSize=24, fontName="Helvetica-Bold",
                               textColor=colors.HexColor("#c4622d"), alignment=TA_CENTER, spaceAfter=4)
    s_sub    = ParagraphStyle("s", fontSize=11, fontName="Helvetica",
                               textColor=colors.HexColor("#888"), alignment=TA_CENTER, spaceAfter=14)
    s_head   = ParagraphStyle("h", fontSize=13, fontName="Helvetica-Bold",
                               textColor=colors.HexColor("#c4622d"), spaceBefore=10, spaceAfter=6)
    s_body   = ParagraphStyle("b", fontSize=10, fontName="Helvetica",
                               textColor=colors.HexColor("#333"), spaceAfter=4)
    s_right  = ParagraphStyle("r", fontSize=10, fontName="Helvetica",
                               textColor=colors.HexColor("#333"), alignment=TA_RIGHT, spaceAfter=4)
    s_due    = ParagraphStyle("d", fontSize=16, fontName="Helvetica-Bold",
                               textColor=colors.HexColor("#c4622d"), alignment=TA_RIGHT, spaceAfter=4)

    # Header
    story.append(Paragraph("PointSphere", s_title))
    story.append(Paragraph("Loyalty Management Platform", s_sub))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c4622d")))
    story.append(Spacer(1, 6*mm))

    # Invoice meta
    meta_rows = [
        ["Invoice Number:", invoice_number],
        ["Date Issued:",    datetime.now().strftime("%d %b %Y")],
        ["Due Date:",       "Immediate"],
        ["Status:",         "OUTSTANDING" if amount_due > 0 else "SETTLED"],
    ]
    meta_table = Table(meta_rows, colWidths=[60*mm, 100*mm])
    meta_table.setStyle(TableStyle([
        ("FONTNAME",    (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME",    (1,0), (1,-1), "Helvetica"),
        ("FONTSIZE",    (0,0), (-1,-1), 10),
        ("TEXTCOLOR",   (0,0), (0,-1), colors.HexColor("#333")),
        ("TEXTCOLOR",   (1,0), (1,-1), colors.HexColor("#555")),
        ("TOPPADDING",  (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0),(-1,-1), 4),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 6*mm))

    # Partner details
    story.append(Paragraph("Billed To", s_head))
    story.append(Paragraph(f"<b>{profile.business_name or partner.name}</b>", s_body))
    story.append(Paragraph(f"Phone: {partner.phone}", s_body))
    story.append(Paragraph(f"Partner Since: {partner.created_at.strftime('%d %b %Y')}", s_body))
    story.append(Spacer(1, 6*mm))

    # Liability entries
    story.append(Paragraph("Outstanding Liability Details", s_head))

    liab_rows = [["Date", "Description", "POS Reference", "Amount (KSh)"]]
    for l in liabilities:
        liab_rows.append([
            l.created_at.strftime("%d %b %Y %H:%M"),
            l.note or "Float shortfall — central pool coverage",
            l.reference or "—",
            f"KSh {float(l.amount):,.2f}",
        ])
    if len(liab_rows) == 1:
        liab_rows.append(["—", "No liabilities recorded", "—", "KSh 0.00"])

    col_w = (A4[0] - 40*mm) / 4
    liab_table = Table(liab_rows, colWidths=[col_w]*4, repeatRows=1)
    liab_table.setStyle(TableStyle([
        ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#c4622d")),
        ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
        ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE",      (0,0), (-1,0), 10),
        ("ALIGN",         (0,0), (-1,0), "CENTER"),
        ("FONTNAME",      (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",      (0,1), (-1,-1), 9),
        ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor("#fff8f4"), colors.white]),
        ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#ddd")),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("ALIGN",         (3,1), (3,-1), "RIGHT"),
    ]))
    story.append(liab_table)
    story.append(Spacer(1, 4*mm))

    # Settlement entries
    if settlements.exists():
        story.append(Paragraph("Settlements Received", s_head))
        sett_rows = [["Date", "Reference", "Note", "Amount (KSh)"]]
        for s in settlements:
            sett_rows.append([
                s.created_at.strftime("%d %b %Y"),
                s.reference or "—",
                s.note or "Settlement",
                f"KSh {float(s.amount):,.2f}",
            ])
        sett_table = Table(sett_rows, colWidths=[col_w]*4, repeatRows=1)
        sett_table.setStyle(TableStyle([
            ("BACKGROUND",    (0,0), (-1,0), colors.HexColor("#16a34a")),
            ("TEXTCOLOR",     (0,0), (-1,0), colors.white),
            ("FONTNAME",      (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE",      (0,0), (-1,-1), 9),
            ("ROWBACKGROUNDS",(0,1), (-1,-1), [colors.HexColor("#f0fdf4"), colors.white]),
            ("GRID",          (0,0), (-1,-1), 0.4, colors.HexColor("#ddd")),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("ALIGN",         (3,1), (3,-1), "RIGHT"),
        ]))
        story.append(sett_table)
        story.append(Spacer(1, 4*mm))

    # Totals
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ddd")))
    story.append(Spacer(1, 4*mm))
    totals = [
        ["Total Liabilities:", f"KSh {float(total_liability):,.2f}"],
        ["Total Settled:",     f"KSh {float(total_settled):,.2f}"],
    ]
    totals_table = Table(totals, colWidths=[120*mm, 50*mm])
    totals_table.setStyle(TableStyle([
        ("FONTNAME",     (0,0), (-1,-1), "Helvetica"),
        ("FONTSIZE",     (0,0), (-1,-1), 10),
        ("ALIGN",        (1,0), (1,-1), "RIGHT"),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("TEXTCOLOR",    (0,0), (-1,-1), colors.HexColor("#555")),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 2*mm))

    due_table = Table([["AMOUNT DUE:", f"KSh {float(amount_due):,.2f}"]], colWidths=[120*mm, 50*mm])
    due_table.setStyle(TableStyle([
        ("FONTNAME",     (0,0), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,-1), 14),
        ("ALIGN",        (1,0), (1,-1), "RIGHT"),
        ("TEXTCOLOR",    (0,0), (-1,-1), colors.HexColor("#c4622d")),
        ("TOPPADDING",   (0,0), (-1,-1), 6),
        ("BOTTOMPADDING",(0,0), (-1,-1), 6),
    ]))
    story.append(due_table)
    story.append(Spacer(1, 8*mm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#c4622d")))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "Please contact PointSphere administration to arrange float settlement. "
        "Continued operations require float balance above the minimum threshold.",
        ParagraphStyle("footer", fontSize=9, fontName="Helvetica",
                       textColor=colors.HexColor("#888"), alignment=TA_CENTER)
    ))

    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="invoice_{invoice_number}.pdf"'
    return response


# ── PARTNER ANALYTICS ────────────────────
@csrf_exempt
@require_http_methods(["GET"])
def partner_analytics(request):
    """Returns time-series data for partner performance charts."""
    user, err = get_user_from_token(request, "partner")
    if err:
        return err

    from django.db.models import Sum, Count
    from django.db.models.functions import TruncDate
    import json as _json

    # Last 30 days of transactions
    from datetime import timedelta
    from django.utils.timezone import now

    thirty_days_ago = now() - timedelta(days=30)

    daily_earn = (
        Transaction.objects
        .filter(partner=user, transaction_type="earn", created_at__gte=thirty_days_ago)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total_points=Sum("points"), count=Count("id"))
        .order_by("day")
    )

    daily_redeem = (
        Transaction.objects
        .filter(partner=user, transaction_type="redeem", created_at__gte=thirty_days_ago)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total_points=Sum("points"), count=Count("id"))
        .order_by("day")
    )

    float_history = (
        FloatTransaction.objects
        .filter(partner=user, created_at__gte=thirty_days_ago)
        .order_by("created_at")
        .values("created_at", "transaction_type", "amount", "balance_after")
    )

    return JsonResponse({
        "daily_earn": [
            {"day": str(r["day"]), "points": r["total_points"], "count": r["count"]}
            for r in daily_earn
        ],
        "daily_redeem": [
            {"day": str(r["day"]), "points": r["total_points"], "count": r["count"]}
            for r in daily_redeem
        ],
        "float_trend": [
            {
                "date": r["created_at"].strftime("%d %b"),
                "type": r["transaction_type"],
                "balance_after": float(r["balance_after"]),
            }
            for r in float_history
        ],
        "summary": {
            "total_earn_30d":   sum(r["total_points"] for r in daily_earn),
            "total_redeem_30d": sum(r["total_points"] for r in daily_redeem),
            "txn_count_30d":    sum(r["count"] for r in daily_earn) + sum(r["count"] for r in daily_redeem),
        }
    })
