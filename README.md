# Akıllı Depo Stok Takip

Depo stokunu blok ve raf olarak tutan bir panel. Üyeler kendi depolarını kurar. Yönetici kayıtları onaylar. Onay, n8n üzerinden e-posta gider.

Sipariş ve iade akışları da bu panelin içindedir. Ayrıntılar proje bitince bu dosyada güncellenecek.

## Çalıştırma

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Panel: http://127.0.0.1:8000

Yönetici girişi: http://127.0.0.1:8000/yonetim

Ayarlar `.env` içindedir. Örnek alanlar `.env.example` dosyasında.
