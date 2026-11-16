// WI-jigup fixture: Spring MVC annotations with no build file declaring
// Spring, so only --frameworks all applies the spring patterns.

package web;

@RestController
public class HealthController {
    @GetMapping("/status")
    public String status() {
        return "ok";
    }
}
