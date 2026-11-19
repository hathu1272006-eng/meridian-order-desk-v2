# Bộ dữ liệu vận hành Meridian

Dữ liệu tổng hợp dùng cho đồ án, mô phỏng đợt nhận đơn ngày 12–17/11/2026. Mã đơn, tài khoản bán, địa chỉ ở cấp thành phố, mã bưu chính, danh mục và giá được đặt để tạo kịch bản nhất quán. Không có thông tin định danh người mua hoặc dữ liệu xuất từ tài khoản sàn thật. Tên dịch vụ giao hàng chỉ mô tả giả định của ca thử.

## Thành phần

- **30 đơn nguồn**, gồm 6 đơn mỗi sàn. Có 25 đơn hợp lệ theo quy tắc nội bộ và 5 đơn lỗi. Ba tệp bổ sung thể hiện gửi lại, thông báo hủy và bản sửa trên đơn đã có.
- **500 SKU** thuộc 10 dòng sản phẩm, phân theo màu, kích thước hoặc quy cách đóng gói. Bộ đơn sử dụng 10 SKU đại diện và mã listing riêng cho từng tài khoản bán.
- [manifest.json](manifest.json) ghi sàn, mã đơn, quốc gia, tiền tệ, kết quả mong đợi và nhóm SKU đã có trên Odoo. [catalog.json](catalog.json) lưu danh mục và [listings.json](listings.json) lưu ánh xạ.
- [meridian-operations.zip](meridian-operations.zip) chứa toàn bộ bộ dữ liệu. Nhập từng tệp trong thư mục `orders` bằng chức năng Xem trước của Meridian.

## Chọn ca để trình diễn

1. **Nghiệm thu Odoo hiện có.** Dùng các tệp `*-01.json` hoặc `*-02.json`. Chúng chỉ dùng `HK-ORG-001` và `ST-NBK-001`, hai SKU đã có trên môi trường thử. Đợt thử dùng năm tiền tệ USD, EUR, ZAR, AUD, CNY. Giữ nguyên tiền tệ nguồn, kể cả Taobao giao Nhật nhưng thanh toán CNY.
2. **Danh mục mở rộng.** Các tệp `*-03.json` đến `*-05.json` hợp lệ trong Meridian. Cần cấu hình thêm tám SKU dùng trong các đơn này trước khi bàn giao Odoo. Trong chế độ cục bộ, chúng có thể dùng để kiểm tra chuẩn hóa, ánh xạ và trạng thái Sẵn sàng.
3. **Ngoại lệ.** Tệp `*-06.json` có một lỗi chính. Hai sàn thiếu ánh xạ sau khi đổi mã biến thể, eBay lệch tổng 1,50 EUR, Etsy có số lượng bằng 0 và AliExpress có giá âm.
4. **Sự kiện sau tiếp nhận.** Sau khi nhập `amazon-01.json`, nhập `amazon-01-replay.json` để kiểm tra nhận diện đúng đơn lặp. Tệp `amazon-01-cancellation.json` ghi nhận hủy cho cùng mã. Dùng `taobao-06-correction.json` làm bản sửa của ca thiếu SKU sau khi phân công, giữ cùng mã đơn nguồn.

Ngày đặt có múi giờ trong dữ liệu nguồn. Thành phố, mã bưu chính và dịch vụ giao hàng dùng để minh họa hồ sơ đơn trong Meridian. Ca tích hợp Odoo hiện vẫn dùng khách hàng thử chung và đối chiếu báo giá, nên các trường giao hàng khảo sát chưa được đồng bộ làm địa chỉ giao trên ERP.
