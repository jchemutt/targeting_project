jQuery(document).ready(function ($) {
    // Back to top button
    $('.back-to-top').on('click', function () {
        $('html, body').animate({
            scrollTop: 0
        }, 1000);
        return false;
    });

    // Initialize AOS animation library if available
    if (typeof AOS !== 'undefined') {
        AOS.init({
            duration: 1000,
            easing: "ease-in-out",
            once: true
        });
    }


    // ✅ Bootstrap 4.5.2 Carousel Auto-slide Fix
    $('#slideshow').carousel({
        interval: 5000, // Auto-slide every 5 seconds
        pause: "hover", // Pause on mouse hover
        wrap: true // Loop back to first image when it reaches the last
    });
});
