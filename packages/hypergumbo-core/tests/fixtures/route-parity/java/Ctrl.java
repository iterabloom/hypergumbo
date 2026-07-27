import org.springframework.web.bind.annotation.*;

@RestController
public class Ctrl {
    @GetMapping("/users")
    public String listUsers() { return "users"; }
}
