# Meridian Order Desk v2

Phiên bản kiến tập nâng cao tiếp nối luồng đơn hàng đa sàn của Meridian. Phạm vi mới tập trung vào đối soát khoản chi trả, xác nhận khắc phục và lưu bằng chứng kiểm thử. Ứng dụng chạy cục bộ với Python 3.12 và SQLite.

## Chạy phiên bản 2

```powershell
python -X utf8 -B -m server --seed-demo
```

Mở [Meridian v2](http://127.0.0.1:8770). Lần chạy đầu tạo ba tài khoản `operator`, `reviewer` và `auditor` với mật khẩu demo riêng được in trong cửa sổ chạy. Giữ lại mật khẩu trong phiên trình diễn. Có thể dùng `--audit-db <tệp SQLite mới>` và `--orders-db <tệp SQLite mới>` để bắt đầu một đợt thử độc lập. Máy chủ chỉ lắng nghe trên địa chỉ loopback.

### Trình diễn kiểm soát đối soát

1. Đăng nhập bằng `operator`, chọn **Nạp kỳ đối soát**, rồi chọn **Dùng dữ liệu mẫu**. Mẫu gồm 8 đơn từ Amazon và eBay, 4 ngoại lệ và 2 đơn chưa đến hạn. Các mã đơn lấy từ bộ dữ liệu mô phỏng Phần 2. Phí, hoàn tiền và khoản chi trả được tạo thêm cho ca thử này.
2. Mở hồ sơ `114-4384545-7122128`. Khoản kỳ vọng là **15,00 USD** và bảng chi trả gốc chưa có dòng tương ứng. Nhập nội dung xử lý tham chiếu `SET-06` và bấm **Gửi soát xét**.
3. Đăng nhập bằng `reviewer`, mở cùng hồ sơ, ghi căn cứ và chọn **Dùng mẫu tái kiểm thử**. Tệp đã bổ sung dòng `SET-06` với **15,00 USD**. Nếu số liệu đạt điều kiện, hồ sơ chuyển sang **Đã đóng**. Có thể tải lại mẫu gốc để minh họa ca chưa đạt và hồ sơ trở về **Đang mở**.
4. Đăng nhập bằng `auditor` và tải gói bằng chứng JSON. Gói gồm kết quả, lịch sử thao tác, nguồn so sánh và SHA-256 của mỗi bộ dữ liệu đã chuẩn hóa. Các tài khoản đọc có thể xem dữ liệu nhưng chỉ các vai trò được giao mới thực hiện từng bước thay đổi.

Mẫu [kỳ gốc](samples/audit/baseline.json) và [tái kiểm thử](samples/audit/retest.json) có cùng dữ liệu đơn nguồn và ngày chốt. Tiền tệ giữ nguyên theo nguồn. Mô hình dùng tiền hàng trừ phí và hoàn tiền, rồi đối chiếu tổng các dòng chi trả tới ngày chốt. Quy tắc đơn giản này dành cho dữ liệu mẫu. Chứng từ tài chính thật có thể cần thêm quy tắc theo từng sàn và kỳ. Gói bằng chứng ghi lại dữ liệu đã nạp, người thao tác và kết quả hệ thống tính, đủ để tái hiện ca thử.

| Thành phần | Vị trí |
| --- | --- |
| Giao diện đối soát | `client/` |
| Đăng nhập, phân quyền, đối soát và lưu hồ sơ | `server/` |
| Luồng nhập đơn và tích hợp Odoo Sales của Phần 2 | `app.py`, `index.html`, `scenarios.py` |
| Dữ liệu mô phỏng và kiểm thử | `samples/`, `check_audit.py`, `check_flow.py` |

Từ trang đối soát, chọn **Luồng đơn hàng** để mở giao diện nghiệp vụ kế thừa. Chế độ mặc định dùng dữ liệu mô phỏng. Để thử Odoo Sales ở luồng đơn hàng, cấu hình môi trường thử như dưới đây rồi thêm `--odoo` vào lệnh chạy phiên bản 2. Đối soát khoản chi trả của phiên bản 2 đọc tệp JSON từ người dùng.

```powershell
python -X utf8 -B -m unittest -v check_flow check_audit
```

## Luồng đơn hàng của Phần 2

Meridian là bản demo phục vụ báo cáo kiến tập về quản lý và vận hành thương mại điện tử với Odoo. Ứng dụng minh họa việc tiếp nhận đơn liên sàn bằng dữ liệu mô phỏng. Cần Python 3.12 và không cần cài thêm thư viện. SQLite lưu đơn, SKU, ánh xạ và lịch sử. Mặc định máy chủ chỉ mở trên loopback.

## Chạy cục bộ

```powershell
python app.py
```

Mở [ứng dụng cục bộ](http://127.0.0.1:8765). Chọn sàn, chọn tệp JSON và bấm **Xem trước**. Kiểm tra SKU, số lượng và đơn giá rồi chọn **Xác nhận nhập** hoặc **Lưu vào ngoại lệ** theo kết quả. Thao tác xem trước chưa lưu đơn hay gửi sang Odoo. Các tệp mẫu có trong mục **Dữ liệu thử nghiệm**. Danh sách có bộ lọc thị trường, trạng thái và người phụ trách. Đơn hợp lệ sẽ ở **Sẵn sàng** khi chưa có kết nối Odoo.

- Năm mẫu đại diện là Amazon → Mỹ, eBay → Đức, AliExpress → Nam Phi, Etsy → Úc và Taobao → Nhật Bản. Đây là các cặp giả lập dùng để kiểm tra adapter và thị trường đích. Đơn Taobao giao Nhật sử dụng CNY theo giả định thanh toán của bộ dữ liệu.
- Mục **Sàn bán hàng** chọn adapter. Quốc gia giao được đọc từ tệp, ưu tiên trường `destination_country` khi tệp có cả mã thị trường sàn và quốc gia giao. Chẳng hạn, tệp Amazon có `Marketplace: "US"`, `destination_country: "DE"`, `CurrencyCode: "USD"` sẽ hiển thị Amazon · Đức · USD. Năm mẫu đại diện không khóa các sàn theo quốc gia.
- **Tiền tệ đơn nguồn** là luồng mặc định duy nhất của mẫu, dùng USD, EUR, ZAR, AUD hoặc CNY. Backend giữ nguyên số tiền và yêu cầu tiền tệ đã bật cùng một bảng giá Odoo phù hợp nếu tiền tệ khác báo giá tham chiếu. Các tệp JSON có thể nhập trực tiếp nằm trong thư mục [samples](samples).
- **Bộ dữ liệu vận hành** có 30 đơn nguồn, ba tệp sự kiện, danh mục 500 SKU và 52 ánh xạ. Trong Dữ liệu thử nghiệm, tải đơn đầu tiên của sàn đang chọn hoặc tải cả gói ZIP. Ngày đặt, thành phố, mã bưu chính và dịch vụ giao xuất hiện khi xem trước và mở chi tiết. Xem [hướng dẫn bộ dữ liệu](samples/operations/README.md).
- Mục **Dữ liệu thử nghiệm** hiện mẫu và ngoại lệ của sàn đang chọn. Trong bộ vận hành, Amazon và Taobao có ca thiếu SKU, eBay có ca lệch tổng, AliExpress có ca đơn giá âm và Etsy có ca số lượng bằng 0.
- Bấm bất cứ ô nào trong dòng đơn để mở chi tiết. Tại dòng thiếu mã, bấm **Ánh xạ SKU**, tìm theo tên hoặc mã SKU, chọn kết quả rồi lưu. App dùng sàn, tài khoản và mã listing từ đơn. Nếu đơn chưa giao người xử lý, bấm **Xử lý lại**. Với đơn đã giao, duyệt theo quy trình bản sửa. Màn hình **Ánh xạ SKU** trên điều hướng dùng để xem và quản lý các cặp mã đã lưu.
- Nếu chỉ thiếu ánh xạ và đơn đã giao, nhập lý do rồi bấm **Gửi ánh xạ để duyệt**. Bản duyệt chứa các dòng và SKU đề xuất, còn tệp gốc giữ nguyên. Đổi ánh xạ sau khi duyệt sẽ yêu cầu xác nhận lại trước bàn giao.
- Để thử hủy, nhập `amazon-cancellation-order-usd.json`, sau đó nhập `amazon-cancellation-notice.json` và bấm **Ghi nhận hủy**. Hai tệp thuộc thư mục [samples/acceptance](samples/acceptance). Đơn xuất hiện ở **Hủy trên sàn** và vẫn mở được báo giá cũ nếu đã có. Meridian không tự hủy báo giá trong Odoo.
- Ca đã nghiệm thu lệch tổng Amazon trong [samples/acceptance](samples/acceptance) có tổng nguồn 52,19 USD và tổng tính lại 50,69 USD. App giữ đơn và hiển thị khoản lệch cùng phí, giảm giá và thuế nguồn.

## Kết nối môi trường Odoo thử nghiệm

Chế độ cục bộ chạy được ngay sau khi tải mã nguồn. Để thử bàn giao báo giá nháp, cần một cơ sở dữ liệu Odoo thử nghiệm có quyền truy cập JSON-2 API, ứng dụng Sales, báo giá mẫu có khách hàng và bảng giá theo tiền tệ của đơn nguồn. Trên PowerShell, dùng thông tin của môi trường thử do bạn quản lý.

~~~powershell
$env:ODOO_URL = 'https://your-test-instance.odoo.com'
$env:ODOO_FIXTURE_QUOTE = 'S00003'
.\Start-Meridian-Odoo.ps1
~~~

Script hỏi khóa API bằng lời nhắc ẩn, tạo hai sản phẩm mẫu nếu cần, rồi chạy tại [127.0.0.1:8766](http://127.0.0.1:8766). Đặt ODOO_FIXTURE_QUOTE theo mã báo giá mẫu trong môi trường của bạn. Nếu cần chạy thử với một file SQLite mới, khởi động app bằng phương án dòng lệnh và thêm --db.

## Kiểm thử tự động

### Chạy kịch bản bàn giao có phê duyệt

1. Chọn eBay rồi nhập `ebay-exception-usd.json` từ [samples/acceptance](samples/acceptance) trên môi trường chưa có mã đơn này. Trong chi tiết đơn, nhập **Mai Linh** và bấm **Giao người xử lý**.
2. Nhập lý do sửa, chọn `ebay-corrected-usd.json` có cùng mã đơn, rồi **Gửi bản sửa**. Xem phần **Trước** và **Sau**.
3. Nhập **Minh An** và bấm **Xác nhận và kiểm tra lại**. Đơn đạt sẽ hiện **Bàn giao Odoo**. Có thể chọn **Trả lại bản sửa** cùng lý do để thử nhánh từ chối.
4. Bấm **Bàn giao Odoo**. Khi backend có kết nối, kiểm tra báo giá nháp và lịch sử xử lý. Ở chế độ cục bộ, đơn vẫn sẵn sàng.

Khi chạy phiên bản Phần 2 riêng bằng `app.py`, tên người xử lý và người duyệt là danh tính mô phỏng nhập tay.

### Chạy bộ test

```powershell
python -B check_flow.py
```

Các ca tự động chạy với SQLite thật và phản hồi Odoo mô phỏng. Bộ kiểm thử bao gồm xem trước chỉ đọc, năm cặp mẫu, luồng giao việc, duyệt bản sửa, duyệt ánh xạ, chặn thay đổi sau duyệt, ghi nhận hủy và kiểm tra bộ dữ liệu vận hành. Các tệp dùng trong nghiệm thu trước nằm ở [samples/acceptance](samples/acceptance).
