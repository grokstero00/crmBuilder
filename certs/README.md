# Дополнительные сертификаты для сборки Docker

Обычно папка пустая. Если `docker compose up --build` падает с ошибкой
`unable to verify the first certificate`, `self-signed certificate in certificate chain`
или `CERTIFICATE_VERIFY_FAILED`, значит антивирус, корпоративный прокси или провайдер
подменяет HTTPS-сертификаты. Положите сюда их корневой сертификат в формате PEM
(файл `.pem` или `.crt`, начинается с `-----BEGIN CERTIFICATE-----`) и пересоберите.

Файлы из этой папки не попадают в git. Сертификат нужен только при сборке образов;
для связи с порталом используется `BITRIX_CA_BUNDLE` (см. README, «Ошибка SSL»).
