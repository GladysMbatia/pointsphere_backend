from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('auth_api', '0002_add_points_partnerprofile_transaction'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='role',
            field=models.CharField(
                choices=[('customer','Customer'),('partner','Partner'),('admin','Admin')],
                default='customer', max_length=20,
            ),
        ),

        # Add min_float_threshold to PartnerProfile
        migrations.AddField(
            model_name='partnerprofile',
            name='min_float_threshold',
            field=models.DecimalField(decimal_places=2, default=1000, max_digits=12),
        ),

        migrations.AddField(
            model_name='transaction',
            name='amount_ksh',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='transaction',
            name='monetary_value',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.AddField(
            model_name='transaction',
            name='pos_reference',
            field=models.CharField(blank=True, default='', max_length=100),
        ),

        # ConversionRate
        migrations.CreateModel(
            name='ConversionRate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('points_per_ksh', models.DecimalField(decimal_places=4, default=1, max_digits=8)),
                ('min_spend_ksh', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('partner', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                 related_name='conversion_rate', to='auth_api.user')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='rates_set', to='auth_api.user')),
            ],
        ),

        # FloatTransaction
        migrations.CreateModel(
            name='FloatTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('transaction_type', models.CharField(
                    choices=[('deposit','Deposit'),('deduction','Deduction'),('reserve','Reserve'),
                             ('liability','Liability'),('settlement','Settlement')],
                    max_length=20)),
                ('amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('balance_after', models.DecimalField(decimal_places=2, max_digits=12)),
                ('note', models.CharField(blank=True, default='', max_length=255)),
                ('reference', models.CharField(blank=True, default='', max_length=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('partner', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='float_transactions', to='auth_api.user')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='float_actions', to='auth_api.user')),
            ],
        ),

        # AuditLog
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('action', models.CharField(max_length=100)),
                ('target', models.CharField(blank=True, default='', max_length=200)),
                ('detail', models.TextField(blank=True, default='')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                           related_name='audit_logs', to='auth_api.user')),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
