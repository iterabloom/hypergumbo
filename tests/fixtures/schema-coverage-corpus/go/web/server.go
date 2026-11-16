// WI-jigup fixture: a gin route, so the ADR-0027 route marker exists on every
// substrate and route_marker_single_home has a symbol to inspect.

package web

import "github.com/gin-gonic/gin"

func health(c *gin.Context) {
	c.JSON(200, "ok")
}

func Register(r *gin.Engine) {
	r.GET("/health", health)
}
