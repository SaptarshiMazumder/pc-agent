"""ad-generation — product photos in, ad images and video clips out.

Layered as domain -> application -> infrastructure -> presentation, under ONE uniquely named
package: the plugin loader puts every plugin's folder on sys.path, so generic top-level names
(`domain`, `application`) would collide between bundles.
"""
