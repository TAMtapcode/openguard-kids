// Lời hứa minh bạch: hiển thị công khai, không cần đăng nhập.
export default function Privacy() {
  return (
    <>
<header className="page-head"><h1>Chúng tôi ghi gì và không ghi gì</h1>
<p className="muted">Open Guardian Kids là công cụ đồng hành, không phải công cụ theo dõi lén. Con cũng thấy được mọi thứ bố mẹ thấy.</p></header>
<section className="grid2">
  <div className="panel"><h2>✅ Có ghi</h2>
    <p>Thời gian dùng máy mỗi ngày.</p><p>Tên ứng dụng đang dùng và thời lượng (ví dụ “Word, 40 phút”).</p>
    <p>Tên miền cấp hai của trang web và thời lượng ước lượng (ví dụ “khanacademy.org”).</p>
    <p>Những lần bị chặn, cảnh báo, khóa máy, yêu cầu thêm giờ của con.</p><p>Ai đã đổi luật, lúc nào, từ máy nào.</p></div>
  <div className="panel"><h2>🚫 Không bao giờ ghi</h2>
    <p>Ảnh chụp màn hình hay nội dung cửa sổ.</p><p>Phím gõ, mật khẩu, nội dung sao chép.</p>
    <p>Tin nhắn, email, cuộc trò chuyện.</p><p>Địa chỉ trang đầy đủ, từ khóa tìm kiếm, tiêu đề cửa sổ.</p><p>Vị trí, camera, micro.</p></div>
</section>
<section className="panel">
  <h2>Minh bạch với con</h2>
  <p>Biểu tượng chiếc lá xanh luôn hiển thị trên máy của con. Mỗi lần chặn đều có giải thích: bị chặn cái gì, thuộc nhóm nào, do ai đặt luật. Con có thể xin thêm giờ, báo chặn nhầm và xem nhật ký đổi luật.</p>
  <p>Cổng thông tin trường học, các trang học trực tuyến quốc gia, y tế và Tổng đài quốc gia bảo vệ trẻ em <b>111</b> không bao giờ bị chặn.</p>
  <h2>Dữ liệu của bạn</h2>
  <p>Dữ liệu hoạt động tự động xóa sau 90 ngày. Bạn có thể xóa ngay mọi lúc ở cuối trang hồ sơ của con. Dữ liệu nằm trên chính máy tính của bạn trong nhà, không gửi ra ngoài internet. Gỡ ứng dụng ở Settings → Apps.</p>
</section>
    </>
  );
}
