from django.db import models
import hashlib
import secrets


class User(models.Model):
    ROLE_CHOICES = [
        ('customer', 'Customer'),
        ('partner', 'Partner'),
        ('admin', 'Admin'),
        ('cashier', 'Cashier'),
    ]

    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='customer')
    pin_hash = models.CharField(max_length=128)
    token = models.CharField(max_length=64, blank=True, null=True)
    points = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('phone', 'role')

    def set_pin(self, pin: str):
        self.pin_hash = hashlib.sha256(pin.encode()).hexdigest()

    def check_pin(self, pin: str) -> bool:
        return self.pin_hash == hashlib.sha256(pin.encode()).hexdigest()

    def generate_token(self) -> str:
        self.token = secrets.token_hex(32)
        return self.token

    def __str__(self):
        return f"{self.name} ({self.role}) – {self.phone}"


class PartnerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='partner_profile')
    business_name = models.CharField(max_length=200, blank=True, default='')
    float_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_float_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=1000)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_float_low(self):
        return self.float_balance <= self.min_float_threshold

    def __str__(self):
        return f"{self.user.name} – float: {self.float_balance}"


class CashierProfile(models.Model):
    """Links a cashier user to a partner."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='cashier_profile')
    partner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cashiers')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.name} (cashier for {self.partner.name})"


class ConversionRate(models.Model):
    """Points awarded per KSh spent — set per partner by admin."""
    partner = models.OneToOneField(User, on_delete=models.CASCADE, related_name='conversion_rate')
    points_per_ksh = models.DecimalField(max_digits=8, decimal_places=4, default=1)  # e.g. 0.1 = 1pt per KSh 10
    min_spend_ksh = models.DecimalField(max_digits=10, decimal_places=2, default=0)   # minimum spend to earn
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='rates_set')

    def calculate_points(self, amount_ksh: float) -> int:
        if amount_ksh < float(self.min_spend_ksh):
            return 0
        return int(amount_ksh * float(self.points_per_ksh))

    def __str__(self):
        return f"{self.partner.name}: {self.points_per_ksh}pt/KSh"


class FloatTransaction(models.Model):
    """Every change to a partner's float — deposit, deduction, or liability."""
    TYPE_CHOICES = [
        ('deposit',   'Deposit'),
        ('deduction', 'Deduction'),   # float deducted on redemption
        ('reserve',   'Reserve'),     # float reserved when points issued
        ('liability', 'Liability'),   # central pool covered a shortfall
        ('settlement','Settlement'),  # partner paid back a liability
    ]

    partner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='float_transactions')
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True, default='')
    reference = models.CharField(max_length=100, blank=True, default='')  # cheque no, mpesa ref, etc
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='float_actions')

    def __str__(self):
        return f"{self.partner.name} {self.transaction_type} KSh {self.amount}"


class Transaction(models.Model):
    TYPE_CHOICES = [
        ('earn',   'Earn'),
        ('redeem', 'Redeem'),
    ]

    customer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='transactions')
    partner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='issued_transactions')
    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='cashier_transactions')
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    points = models.IntegerField()
    amount_ksh = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # original spend amount
    monetary_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # KSh value of points
    note = models.CharField(max_length=255, blank=True, default='')
    pos_reference = models.CharField(max_length=100, blank=True, default='')  # POS transaction ID
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.customer.name} {self.transaction_type} {self.points} pts"


class AuditLog(models.Model):
    """Immutable log of every significant system action."""
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_logs')
    action = models.CharField(max_length=100)   # e.g. 'login', 'points_earn', 'float_deposit'
    target = models.CharField(max_length=200, blank=True, default='')  # e.g. 'customer:0712345678'
    detail = models.TextField(blank=True, default='')  # JSON string with extra context
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user} – {self.action} at {self.created_at}"
