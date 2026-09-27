from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        style={"input_type": "password"},
    )
    email = serializers.EmailField(required=True)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password")
        read_only_fields = ("id",)

    def validate_username(self, value):
        trimmed = value.strip()
        if User.objects.filter(username__iexact=trimmed).exists():
            raise serializers.ValidationError("An account with these details already exists.")
        return trimmed

    def validate_email(self, value):
        normalized = value.strip().lower()
        if User.objects.filter(email__iexact=normalized).exists():
            raise serializers.ValidationError("An account with these details already exists.")
        return normalized

    def validate_password(self, value):
        # Run Django's standard configured password validators
        validate_password(value)
        return value

    def create(self, validated_data):
        username = validated_data["username"].strip()
        email = validated_data["email"].strip().lower()
        try:
            with transaction.atomic():
                if User.objects.filter(email__iexact=email).exists():
                    raise serializers.ValidationError(
                        "An account with these details already exists."
                    )
                user = User.objects.create_user(
                    username=username,
                    email=email,
                    password=validated_data["password"],
                )
                return user
        except IntegrityError:
            raise serializers.ValidationError("An account with these details already exists.")


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email")
        read_only_fields = ("id", "username", "email")


class CaseInsensitiveTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Allows users to log in with their username regardless of character case.
    """

    def validate(self, attrs):
        username = attrs.get(self.username_field)
        if username:
            user = User.objects.filter(username__iexact=username).first()
            if user:
                attrs[self.username_field] = user.get_username()
        return super().validate(attrs)
