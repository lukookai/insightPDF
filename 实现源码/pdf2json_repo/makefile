.PHONY: zip

zip:
	- pyarmor gen .
	mkdir -p dist/fonts
	# cp -r ./temp/fonts/* dist/fonts
	zip -r dist.zip dist/
	rm -rf ./pyarmor_output/
