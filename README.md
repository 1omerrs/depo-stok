# Depo Stok

Depo Stok, bir satıcının deposunu blok ve raf düzeninde tutan bir stok panelidir. Trendyol, Hepsiburada, n11 ve satıcının kendi sitesinden gelen siparişler aynı listede toplanır. Stok adedi, sipariş depoya düştüğü anda değil, satıcı onayladığında değişir.

Uygulama pazaryerinde satış yapmaz ve mağaza şifresi istemez. Görevi, satılan ürünün depodaki karşılığını göstermek ve onaylanan çıkışı raftan düşmektir.

## Amaç

Küçük ve orta ölçekli bir satıcı çoğu zaman birden fazla kanalda satış yapar. Her kanalın kendi sipariş ekranı vardır; depodaki adet ise ayrı bir defterde, tabloda ya da hafızada durur. Depo Stok bu iki tarafı ayırır: kanal siparişi getirir, depo kararı stoku değiştirir.

Her üyenin deposu kendisine aittir. Bir satıcının rafları, ürünleri ve siparişleri başka bir satıcının panelinde görünmez. Yönetici, üyelik başvurularını ayrı bir ekrandan onaylar.

## Çözdüğü sorunlar

- Sipariş düşünce stok kendiliğinden azalmaz. Yanlış eşleşen ya da henüz hazır olmayan bir sipariş, raftaki adedi bozmaz.
- Aynı sipariş numarasına ait birden fazla ürün tek kartta durur. Karttaki ürünlerin hepsi seçilmeden sipariş onaylanmaz ve stok parça parça düşmez.
- Stokta olmayan bir ürün siparişi listeden silinmez. Kartta yalnızca stokta olmadığı yazılır. Ürün daha sonra rafa eklenirse bekleyen sipariş güncel adedi görür.
- Son 30 günün giriş ve çıkışları stok sayfasında özetlenir. Giriş, rafa eklenen adettir. Çıkış, onaylanan sipariş, elle azaltma ya da raftan silmedir.
- Düşük stok ve kopan kanal bağlantısı, kurulu bir n8n akışı varsa satıcıya e-posta olarak iletilebilir. Günlük özet, bekleyen siparişi veya eşiğin altına inmiş stoğu olan onaylı üyelere gider.
- Kendi sitesinden gelen sipariş, üyeye özel bir geliş adresine yazılır. Adres, o üyenin deposuna aittir.

## Nasıl çalışır

1. Satıcı üye olur. Yönetici başvuruyu onaylar.
2. Satıcı blok ve raf açar, ürünleri raflara yazar.
3. Bağlı kanallardan ya da kendi sitesinden sipariş listeye düşer.
4. Ürün, stok kodu, barkod ya da ürün adıyla depodaki kayıtla eşleşir. Emin olunmayan satır, katalogdan seçilmeyi bekler.
5. Satıcı onaylar. Yeterli stok varsa adet raftan düşer ve sipariş tamamlanır. Yeterli stok yoksa adet değişmez.

## Teknolojiler

| Katman | Seçim |
| --- | --- |
| Sunucu | Python, FastAPI, Uvicorn |
| Veri | SQLite. İstenirse Airtable |
| Arayüz | Tek sayfalık HTML, CSS ve JavaScript |
| Oturum | İmzalı çerez, parola için PBKDF2 |
| Dış istek | HTTPX |
| Bildirim ve zamanlama | n8n |
| Eşleştirme | Stok kodu, barkod ve ada göre kesin eşleşme. Anahtar tanımlıysa isteğe bağlı dil modeli, aksi halde metin benzerliği |
| Test | pytest |

Arayüz sunucunun statik dosyalarından gelir. Ayrı bir ön yüz derlemesi yoktur.

## Çalıştırma

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Panel: http://127.0.0.1:8000

Yönetici girişi: http://127.0.0.1:8000/yonetim

Ayarlar `.env` dosyasındadır. Alanların anlamı `.env.example` içindedir. `.env` depoya eklenmez.

## Kapsam sınırı

Amazon satıcı kimliği saklanır; Amazon siparişleri henüz otomatik çekilmez. Sipariş iptali ve parola sıfırlama bu sürümde yoktur. İade kaydı panelde durur; iade için ayrı bir n8n akışı gerekmez.
